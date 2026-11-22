"""Build on native Windows. Linux cannot cross-build a Windows PyInstaller app."""
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform!='win32':raise SystemExit('Run this build on Windows x64')
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir',
             '--console','--name','Biography','--paths',str(root/'src')]
    for package in ['faster_whisper','ctranslate2','tokenizers','sherpa_onnx','sherpa_onnx_core',
                    'llama_cpp','pydantic_core','av','onnxruntime']:
        command.extend(['--collect-all',package])
    for module in ['biography.cli','biography.gui','biography.sherpa_backend','biography.asr_worker']:
        command.extend(['--hidden-import',module])
    command.append(str(root/'packaging'/'windows_entry.py'))
    subprocess.run(command,cwd=root,check=True)


if __name__=='__main__':main()
