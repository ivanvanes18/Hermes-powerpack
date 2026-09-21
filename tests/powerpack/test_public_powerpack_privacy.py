import re
from pathlib import Path


def test_powerpack_public_skills_exist():
    root = Path(__file__).resolve().parents[2]
    required = [
        "skills/decision/SKILL.md",
        "skills/reasoning-personas/SKILL.md",
        "skills/rp/SKILL.md",
        "skills/superpowers/SKILL.md",
        "skills/productivity/present/SKILL.md",
        "skills/media/piapi-video-toolkit/SKILL.md",
        "skills/telegram/telegram-chip/SKILL.md",
        "scripts/install-powerpack.sh",
        "README-POWERPACK.md",
        "WORKSHOP-SKILLS.md",
        "skills/po/SKILL.md",
        "skills/perplex/SKILL.md",
        "skills/par/SKILL.md",
        "skills/design/refero-web-design/SKILL.md",
        "skills/hallmark/SKILL.md",
        "skills/design-pack/taste-skill/SKILL.md",
        "skills/webd/SKILL.md",
        "skills/supergoal/SKILL.md",
        "skills/devops/server-doctor/SKILL.md",
        "skills/devops/public-endpoint-ops/SKILL.md",
        "skills/infrastructure/porkbun-api-dns/SKILL.md",
        "skills/devops/supabase-project-ops/SKILL.md",
    ]
    missing = [p for p in required if not (root / p).exists()]
    assert not missing


def test_public_powerpack_files_do_not_include_private_markers():
    root = Path(__file__).resolve().parents[2]
    scanned_roots = [
        root / "README-POWERPACK.md",
        root / "WORKSHOP-SKILLS.md",
        root / "skills",
        root / "scripts",
    ]
    forbidden = [
        "Human" + "20",
        "human" + "20",
        "Human " + "2.0",
        "Chip" + "CR",
        "chip" + "manager",
        "617" + "744" + "661",
        "-100" + "3712304136",
        "-100" + "4069237649",
        "185" + ".212.129.177",
        "72" + ".56.32.125",
        "178" + ".63.60.99",
        "138" + ".201.30.209",
        "/home/" + "chip",
        "/opt/" + "telegram-chip",
        "/opt/" + "workshop-bot",
        "human" + "20team",
        "Evgeny " + "Yurchenko",
        "e." + "yurchenko",
        "BEGIN " + "PRIVATE KEY",
    ]
    secret_patterns = {
        "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        "openai_style_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        "telegram_bot_token": re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
        "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }
    offenders = []
    for base in scanned_roots:
        paths = [base] if base.is_file() else list(base.rglob("*"))
        for path in paths:
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".ico", ".woff", ".woff2", ".ttf"
            }:
                continue
            text = path.read_text(errors="ignore")
            for marker in forbidden:
                if marker in text:
                    offenders.append((str(path.relative_to(root)), "marker", marker))
            for name, pattern in secret_patterns.items():
                if pattern.search(text):
                    offenders.append((str(path.relative_to(root)), "secret-pattern", name))
    assert offenders == []
