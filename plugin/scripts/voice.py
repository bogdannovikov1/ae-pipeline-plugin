#!/usr/bin/env python3
"""Голос из текста через ElevenLabs: проверка тегов, озвучка голосом автора, EQ через ffmpeg.

Ключ API лежит в Связке ключей macOS (служба из voice.keychain_service):
    security add-generic-password -U -a "$USER" -s ae-video-elevenlabs -w

Пример:
    python3 voice.py models
    python3 voice.py check F1.voice.txt
    python3 voice.py generate F1.voice.txt --out F1.wav --raw F1.voice_raw.wav
"""
import argparse
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from settings import load_settings

TAG = re.compile(r"\[([^\[\]]*)\]")


class VoiceError(Exception):
    pass


def ssl_context():
    """Python с python.org не видит сертификаты macOS: берём certifi или системный набор."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    system = Path("/etc/ssl/cert.pem")
    return ssl.create_default_context(cafile=str(system)) if system.is_file() else ssl.create_default_context()


def tag_errors(text, allowed):
    """Ошибки разметки: теги только из allowed, через запятую внутри квадратных скобок."""
    allowed = {tag.lower() for tag in allowed}
    errors = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in TAG.finditer(line):
            for tag in (part.strip() for part in match.group(1).split(",")):
                if tag.lower() not in allowed:
                    errors.append(f"строка {number}: тег «{tag}» не разрешён")
        rest = TAG.sub("", line)
        if "[" in rest or "]" in rest:
            errors.append(f"строка {number}: незакрытая квадратная скобка")
    return errors


def notation_errors(text, pattern, spoken=None):
    """Формулы и символы голос читает буквально: их пишут так, как произносят."""
    if not pattern:
        return []
    errors = []
    for number, line in enumerate(TAG.sub("", text).splitlines(), 1):
        for match in re.finditer(pattern, line):
            hint = (spoken or {}).get(match.group(0))
            fix = f"«{hint}»" if hint else "как произносится (voice.spoken_forms)"
            errors.append(f"строка {number}: «{match.group(0)}» голос прочтёт буквально — запиши {fix}")
    return errors


def text_errors(text, cfg):
    """Все проверки текста перед озвучкой: теги и формулы."""
    return tag_errors(text, cfg["allowed_tags"]) + notation_errors(text, cfg.get("notation_pattern"), cfg.get("spoken_forms"))


def plain_text(text):
    """Текст без тегов: подсказка для распознавания речи."""
    return re.sub(r"[ \t]+", " ", TAG.sub("", text)).strip()


def eq_filter(bands):
    """Цепочка фильтров ffmpeg из полос {freq, gain, q}."""
    return ",".join(f"equalizer=f={b['freq']}:t=q:w={b['q']}:g={b['gain']}" for b in bands)


def chunks(text, max_chars):
    """Режет текст по строкам на куски не длиннее max_chars; длинную строку — по предложениям."""
    pieces = []
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        if len(line) <= max_chars:
            pieces.append(line)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?…])\s+", line):
            if current and len(current) + 1 + len(sentence) > max_chars:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)
    result, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > max_chars:
            result.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}".strip()
    if current:
        result.append(current)
    return result


def api_key(service):
    run = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    if run.returncode != 0 or not run.stdout.strip():
        raise VoiceError(
            f"Нет ключа ElevenLabs в Связке ключей (служба {service}). Положите его командой:\n"
            f'security add-generic-password -U -a "$USER" -s {service} -w'
        )
    return run.stdout.strip()


def request(cfg, key, method, path, query=None, body=None):
    url = cfg["api_url"].rstrip("/") + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"], context=ssl_context()) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise VoiceError(f"ElevenLabs {error.code}: {error.read().decode(errors='replace')[:500]}") from None
    except urllib.error.URLError as error:
        raise VoiceError(f"Нет связи с ElevenLabs: {error.reason}") from None


def list_models(cfg, key):
    return json.loads(request(cfg, key, "GET", "/v1/models"))


def find_voice(cfg, key, name):
    if not name:
        raise VoiceError("Не задан голос: настройка voice.voice_name")
    found = json.loads(request(cfg, key, "GET", "/v2/voices", {"search": name, "page_size": cfg["search_page_size"]})).get("voices", [])
    exact = [v for v in found if v.get("name", "").strip().lower() == name.strip().lower()]
    if not exact:
        names = ", ".join(v.get("name", "?") for v in found) or "ничего"
        raise VoiceError(f"Голос «{name}» не найден. По поиску нашлось: {names}")
    return exact[0]["voice_id"]


def to_pcm(audio, fmt, rate):
    """Любой ответ API → сырой PCM s16le моно с частотой voice.pcm_rate."""
    if fmt == f"pcm_{rate}":
        return audio
    run = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ar", str(rate), "-ac", "1", "pipe:1"],
                         input=audio, capture_output=True)
    if run.returncode != 0:
        raise VoiceError(f"ffmpeg не разобрал ответ: {run.stderr.decode(errors='replace')[:300]}")
    return run.stdout


def synthesize(cfg, key, voice_id, text, voice_settings):
    """Озвучивает кусок текста. Если тариф не даёт PCM, берёт запасной формат."""
    body = {"text": text, "model_id": cfg["model"], "voice_settings": voice_settings}
    last = None
    for fmt in cfg["formats"]:
        try:
            audio = request(cfg, key, "POST", f"/v1/text-to-speech/{voice_id}", {"output_format": fmt}, body)
            return to_pcm(audio, fmt, cfg["pcm_rate"])
        except VoiceError as error:
            last = error
            if "output_format" not in str(error) and "format" not in str(error).lower():
                raise
    raise last


def keep_take(out):
    """Прошлый дубль не затирается: он становится <имя>.take<N>.wav."""
    out = Path(out)
    if not out.is_file():
        return None
    number = 1
    while (taken := out.with_name(f"{out.stem}.take{number}{out.suffix}")).exists():
        number += 1
    out.rename(taken)
    return taken


def write_wav(pcm, out, cfg, eq=None):
    """PCM → WAV с частотой voice.sample_rate и кодеком voice.codec, с EQ или без."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(cfg["pcm_rate"]), "-ac", "1", "-i", "pipe:0"]
    if eq:
        command += ["-af", eq_filter(eq)]
    command += ["-ar", str(cfg["sample_rate"]), "-c:a", cfg["codec"], str(out)]
    run = subprocess.run(command, input=pcm, capture_output=True)
    if run.returncode != 0:
        raise VoiceError(f"ffmpeg: {run.stderr.decode(errors='replace')[:300]}")
    return out


def generate(text, out, cfg, raw=None, voice_settings=None):
    """Проверяет теги, озвучивает текст кусками, пишет файл с EQ и, если просили, сырой."""
    errors = text_errors(text, cfg)
    if errors:
        raise VoiceError("Текст не прошёл проверку, запрос не отправлен:\n" + "\n".join(errors))
    if not cfg["model"]:
        raise VoiceError("Не задана модель: настройка voice.model (список: voice.py models)")
    key = api_key(cfg["keychain_service"])
    voice_id = find_voice(cfg, key, cfg["voice_name"])
    settings = dict(cfg["voice_settings"], **(voice_settings or {}))
    pcm = b"".join(synthesize(cfg, key, voice_id, piece, settings) for piece in chunks(text, cfg["max_chars"]))
    if raw:
        write_wav(pcm, raw, cfg)
    keep_take(out)
    return write_wav(pcm, out, cfg, cfg["eq"])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["models", "check", "plain", "generate"])
    parser.add_argument("text", nargs="?", type=Path, help="файл текста с тегами")
    parser.add_argument("--out", type=Path, help="для generate: итоговый WAV с EQ")
    parser.add_argument("--raw", type=Path, help="для generate: тот же дубль без EQ")
    parser.add_argument("--stability", type=float, help="для generate: заменить stability из настроек")
    parser.add_argument("--video", type=Path, help="папка видео с её настройками")
    args = parser.parse_args()
    cfg = load_settings(args.video)["voice"]

    try:
        if args.command == "models":
            for model in list_models(cfg, api_key(cfg["keychain_service"])):
                print(f"{model['model_id']}: {model.get('name', '')} — {model.get('description', '')[:120]}")
            return
        if not args.text:
            parser.error("укажите файл текста")
        text = args.text.read_text(encoding="utf-8")
        if args.command == "plain":
            print(plain_text(text))
            return
        if args.command == "check":
            errors = text_errors(text, cfg)
            for error in errors:
                print(f"Ошибка: {error}")
            if errors:
                sys.exit(1)
            print(f"Текст в порядке, кусков для озвучки: {len(chunks(text, cfg['max_chars']))}")
            return
        if not args.out:
            parser.error("укажите --out")
        extra = {"stability": args.stability} if args.stability is not None else None
        path = generate(text, args.out, cfg, args.raw, extra)
        print(f"Готово: {path}" + (f", без EQ: {args.raw}" if args.raw else ""))
    except VoiceError as error:
        sys.exit(f"Отказ: {error}")


if __name__ == "__main__":
    main()
