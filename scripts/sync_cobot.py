#!/usr/bin/env python3
"""Publish committed runtime files from A6000; never restart any service."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = "/home/agilex/jiaan/project/cobot-web"
def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="agilex@10.7.165.64")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    args = parser.parse_args()
    if args.target != DEFAULT_TARGET:
        parser.error("This deployment entry targets the registered Cobot web directory")
    run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT)
    revision = run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True).stdout.strip()
    names = run(["git","ls-files","-z"],cwd=ROOT,capture_output=True).stdout.split("\0")
    roots = {"app","scripts","robot","configs","integrations","docs"}
    files = [name for name in names if name and
        (Path(name).parts[0] in roots or name in ("README.md","AGENTS.md","pyproject.toml","uv.lock"))
        and "tests" not in Path(name).parts and "global-preview" not in Path(name).parts]
    hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    evidence=ROOT/"outputs/deployments";evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",dir=evidence) as listing:
        listing.write("\n".join(files)+"\n");listing.flush()
        run(["rsync","-a","--no-owner","--no-group","--files-from="+listing.name,
             "-e","ssh -o BatchMode=yes",str(ROOT)+"/",args.host+":"+args.target+"/"])
    verify = "from pathlib import Path\nimport hashlib,json\nroot=Path("+repr(args.target)+")\n"
    verify += "expected="+repr(hashes)+"\n"
    verify += "bad=[p for p,h in expected.items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]\n"
    verify += "assert not bad, bad\n"
    verify += "receipt="+repr({"revision":revision,"files":len(files),"target":args.target,"hashes":hashes})+"\n"
    verify += "(root/'.release.json').write_text(json.dumps(receipt,indent=2))\nprint('Verified',len(expected),'runtime files; services unchanged')\n"
    result=run(["ssh","-o","BatchMode=yes",args.host,"python3","-"],input=verify,capture_output=True)
    (evidence/(revision+".json")).write_text(json.dumps({"host":args.host,"revision":revision,"target":args.target,"hashes":hashes},indent=2))
    print(result.stdout.strip())

if __name__=="__main__":
    main()
