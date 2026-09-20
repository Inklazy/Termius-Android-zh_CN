"""Release orchestration only; APK processing remains in apktools.py."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

TAG = re.compile(r"^android-v(.+)-(\d+)$")


def gh(*args):
    # Do not reinterpret API/network/authentication errors as an empty release list.
    return subprocess.check_output(["gh", *args], text=True, encoding="utf-8")


def releases():
    pages = json.loads(gh("api", "--paginate", "--slurp",
                          f"repos/{os.environ['GITHUB_REPOSITORY']}/releases?per_page=100"))
    return [release for page in pages for release in page]


def published_versions(items):
    result = []
    for release in items:
        match = TAG.fullmatch(release["tag_name"])
        if match and not release["draft"] and not release["prerelease"]:
            name, code = match.groups()
            asset = f"Termius_v{name}_zh_CN.apk"
            if any(a["name"] == asset and a.get("state") == "uploaded"
                   and a.get("size", 0) > 0 for a in release.get("assets", [])):
                result.append((int(code), name))
    return result


def identity(metadata):
    if metadata["package_name"] != "com.server.auditor.ssh.client":
        raise ValueError("Unexpected Google Play package")
    name, code = metadata["version_name"], metadata["version_code"]
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", name):
        raise ValueError("Unsafe versionName for release/asset filename")
    if not re.fullmatch(r"[0-9]+", code) or int(code) <= 0:
        raise ValueError("Invalid APK versionCode")
    return f"android-v{name}-{code}", f"Termius_v{name}_zh_CN.apk"


def decision(metadata, items, force):
    versions = published_versions(items)
    latest = max(versions, default=None)
    print("Google Play:\npackage={package_name}\nversionName={version_name}\n"
          "versionCode={version_code}".format(**metadata), flush=True)
    print("Latest published Android release:\n" + (
        f"versionName={latest[1]}\nversionCode={latest[0]}" if latest else
        "versionName=none\nversionCode=none"), flush=True)
    code = int(metadata["version_code"])
    exists = any(v[0] == code for v in versions)
    older = latest is not None and code < latest[0]
    if force:
        print("Decision: force build (Artifact only)", flush=True)
        return True, False
    if exists or older:
        print("Decision: " + ("already published" if exists else "older Google Play version"), flush=True)
        print(f"No new Google Play version. Current: {metadata['version_name']} "
              f"({code}), skipping build.", flush=True)
        return False, False
    print("Decision: new version", flush=True)
    return True, True


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def build():
    from apktools import TermiusAPKModifier
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
    publish = False

    def gate(metadata):
        nonlocal publish
        identity(metadata)
        proceed, publish = decision(metadata, releases(), force)
        return proceed

    metadata = TermiusAPKModifier().modify_apk(before_build=gate)
    if metadata is None:
        output(built=False, publish=False)
        return
    tag, asset = identity(metadata)
    out = Path(__file__).resolve().parent / "out"
    shutil.copy2(out / "Termius.apk", out / asset)
    # Written only after modify_apk has completed all final verification.
    (out / "release-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    output(built=True, publish=publish, tag=tag, asset=asset)


def publish():
    out = Path(__file__).resolve().parent / "out"
    metadata = json.loads((out / "release-metadata.json").read_text(encoding="utf-8"))
    tag, asset = identity(metadata)
    items = releases()
    code = int(metadata["version_code"])
    if any(v[0] >= code for v in published_versions(items)):
        print("Release already published or superseded; leaving history unchanged.")
        return
    # Never overwrite even an incomplete existing release. A failed upload leaves
    # a draft that needs explicit inspection/removal before a retry.
    if any(r["tag_name"] == tag for r in items):
        raise RuntimeError(f"Release {tag} already exists (possibly a failed draft); inspect it manually")
    apk = out / asset
    if not apk.is_file() or apk.stat().st_size == 0:
        raise RuntimeError("Verified APK asset missing")
    notes = out / "release-notes.md"
    notes.write_text(
        f"Termius versionName: {metadata['version_name']}\n\n"
        f"versionCode: {metadata['version_code']}\n\nSource: Google Play\n\n"
        f"中文构建版本: {tag}\n\n构建提交: {os.environ['GITHUB_SHA']}\n\n"
        f"构建时间 (UTC): {datetime.now(timezone.utc).isoformat()}\n\n"
        "签名说明：由本仓库固定自定义 JKS 签名，不能直接覆盖 Google Play 官方版。\n\n"
        "安装前请备份数据；官方版可能需要卸载后才能安装。相同自定义签名的版本可尝试覆盖升级。"
        "请自行确认设备兼容性，仅下载 APK 文件安装。\n", encoding="utf-8")
    gh("release", "create", tag, "--repo", os.environ["GITHUB_REPOSITORY"],
       "--target", os.environ["GITHUB_SHA"], "--draft", "--title",
       f"Termius Android 中文版 v{metadata['version_name']}", "--notes-file", str(notes))
    gh("release", "upload", tag, str(apk), "--repo", os.environ["GITHUB_REPOSITORY"])
    gh("release", "edit", tag, "--draft=false", "--latest=false",
       "--repo", os.environ["GITHUB_REPOSITORY"])
    print(f"Published {tag} with {asset}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "publish"))
    args = parser.parse_args()
    {"build": build, "publish": publish}[args.command]()
