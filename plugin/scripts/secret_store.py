#!/usr/bin/env python3
"""Хранит ключи API в системном хранилище: Связка ключей на macOS, Диспетчер учётных данных на Windows.

Пример:
    python3 secret_store.py set ae-video-elevenlabs
    python3 secret_store.py check ae-video-elevenlabs
"""
import argparse
import getpass
import subprocess
import sys

ACCOUNT = "ae-video"


def is_mac():
    return sys.platform == "darwin"


def get(service):
    """Ключ службы или None, если его нет."""
    if is_mac():
        run = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        return run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else None
    try:
        import keyring
    except ImportError:
        return None
    return keyring.get_password(service, ACCOUNT) or None


def store(service, value):
    if is_mac():
        subprocess.run(["security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", service, "-w", value], check=True)
        return
    import keyring
    keyring.set_password(service, ACCOUNT, value)


def hint(service):
    """Команда, которой автор кладёт ключ."""
    if is_mac():
        return f'security add-generic-password -U -a "$USER" -s {service} -w'
    return f"python {__file__} set {service}"


def where():
    return "Связке ключей" if is_mac() else "Диспетчере учётных данных Windows"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["set", "check"])
    parser.add_argument("service", help="имя службы, например voice.keychain_service из настроек")
    args = parser.parse_args()
    if args.command == "check":
        found = get(args.service) is not None
        print("ключ есть" if found else f"ключа нет: {hint(args.service)}")
        sys.exit(0 if found else 1)
    value = getpass.getpass("Ключ (ввод не отображается): ").strip()
    if not value:
        sys.exit("Пустой ключ, ничего не сохранено")
    store(args.service, value)
    print(f"Сохранено в {where()}")


if __name__ == "__main__":
    main()
