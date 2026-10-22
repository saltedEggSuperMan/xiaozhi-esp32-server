"""Build an Otto resource-only update for the verified ST7789 v2.2.4 image."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / '.runtime/fw244'
sys.path.insert(0, str(SDK / 'asset-tools'))
from PIL import Image, ImageSequence

OUT = ROOT / 'artifacts/otto-st7789-v2.2.4'
WORK = ROOT / '.runtime/otto-build'
SOURCE = SDK / 'src/managed_components/txp666__otto-emoji-gif-component'
BASE = ROOT / 'artifacts/firmware-st7789-v2.2.4/generated_assets.bin'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unpack(data):
    count, checksum, length = struct.unpack_from('<III', data)
    assert length == len(data) - 12
    assert sum(data[12:]) & 0xffff == checksum
    result = {}
    start = 12 + count * 44
    spans = []
    for i in range(count):
        name, size, offset, _, _ = struct.unpack_from('<32sIIHH', data, 12 + i * 44)
        name = name.split(b'\0', 1)[0].decode()
        assert Path(name).name == name and name not in result
        pos = start + offset
        assert pos >= start and pos + 2 + size <= len(data)
        assert data[pos:pos + 2] == b'ZZ'
        spans.append((pos, pos + 2 + size))
        result[name] = data[pos + 2:pos + 2 + size]
    assert all(a[1] <= b[0] for a, b in zip(sorted(spans), sorted(spans)[1:]))
    return result


def gif_info(path):
    with Image.open(path) as image:
        durations = []
        for frame in ImageSequence.Iterator(image):
            frame.load()
            durations.append(frame.info.get('duration', 80))
        return {'size': list(image.size), 'frames': len(durations),
                'duration_ms': sum(durations), 'loop': image.info.get('loop')}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    assets = WORK / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    original = unpack(BASE.read_bytes())
    index = json.loads(original['index.json'])
    old_emoji = {e['file'] for e in index['emoji_collection']}
    kept = {n: data for n, data in original.items() if n not in old_emoji and n != 'index.json'}
    assert index.get('srmodels') and index.get('text_font')
    for name, data in kept.items():
        (assets / name).write_bytes(data)

    # A fixed grayscale palette keeps the monochrome Otto style and frame colors stable.
    palette = Image.new('P', (1, 1))
    palette.putpalette([value for v in range(256) for value in (v, v, v)])
    metadata = []
    entries = []
    gifs = sorted((SOURCE / 'gifs').glob('*.gif'))
    assert len(gifs) == 21
    for source in gifs:
        target = assets / source.name
        frames, durations = [], []
        with Image.open(source) as image:
            for frame in ImageSequence.Iterator(image):
                durations.append(frame.info.get('duration', 80))
                resized = frame.convert('RGB').resize((128, 128), Image.Resampling.LANCZOS)
                quantized = resized.quantize(palette=palette, dither=Image.Dither.NONE)
                # RGB conversion may retain a tuple transparency value; every frame is opaque.
                quantized.info.clear()
                frames.append(quantized)
        frames[0].save(target, save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, disposal=2, optimize=False)
        before, after = gif_info(source), gif_info(target)
        assert after['size'] == [128, 128] and after['loop'] == 0
        assert before['duration_ms'] == after['duration_ms'] and after['frames'] > 1
        entries.append({'name': source.stem, 'file': source.name})
        metadata.append({'file': source.name, 'original': before, 'packed': after,
                         'source_sha256': sha(source.read_bytes()), 'sha256': sha(target.read_bytes())})
    index['emoji_collection'] = entries
    # Both themes use the animation's black background and readable white text.
    index['skin'] = {theme: {'background_color': '#000000', 'text_color': '#FFFFFF'}
                     for theme in ('light', 'dark')}
    (assets / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')

    spec = importlib.util.spec_from_file_location('upstream_assets', SDK / 'src/scripts/build_default_assets.py')
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    dest = OUT / 'otto-assets.bin'
    # Pack into a fresh directory so rerunning never includes stale temporary files.
    selected = WORK / 'selected'
    selected.mkdir(exist_ok=True)
    expected_names = set(kept) | {'index.json'} | {e['file'] for e in entries}
    for name in expected_names:
        shutil.copyfile(assets / name, selected / name)
    assert {p.name for p in selected.iterdir()} == expected_names, 'Unexpected staged resource'
    upstream.pack_assets_simple(str(selected), str(WORK / 'include'), str(dest), 'assets', 32)
    built = unpack(dest.read_bytes())
    assert set(built) == expected_names
    assert all(built[name] == data for name, data in kept.items()), 'Font or wake model changed'
    assert json.loads(built['index.json'])['emoji_collection'] == entries
    assert len({e['name'] for e in entries}) == 21
    assert all(built[e['file']] == (selected / e['file']).read_bytes() for e in entries)
    assert 0 < dest.stat().st_size <= 8 * 1024 * 1024
    shutil.copyfile(BASE, OUT / 'restore-default-assets.bin')
    shutil.copyfile(SOURCE / 'LICENSE', OUT / 'OTTO-LICENSE.txt')
    shutil.copyfile(assets / 'neutral.gif', OUT / 'preview-neutral.gif')
    with Image.open(assets / 'neutral.gif') as image:
        image.convert('RGB').save(WORK / 'preview-neutral.png')
    manifest = {'firmware': '2.2.4 bread-compact-wifi-lcd', 'chip': 'esp32s3',
                'display': 'ST7789 240x240', 'emoji_size': [128, 128], 'count': 21,
                'flash_offset': '0x800000', 'partition_size': '0x800000',
                'assets_bytes': dest.stat().st_size, 'sha256': sha(dest.read_bytes()),
                'restore_sha256': sha(BASE.read_bytes()),
                'preserved_files': {n: sha(data) for n, data in kept.items()},
                'source': 'https://github.com/txp666/otto-emoji-gif-component',
                'source_commit': '4279001383c2b1a39ed0457b14759e7c87537941', 'animations': metadata}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (OUT / 'flash-otto.ps1').write_text(r'''param([string]$Port='COM3', [switch]$Restore)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python=Join-Path $projectRoot '.runtime/fw244/tools/python_env/idf5.5_py3.10_env/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project ESP-IDF Python not found.' }
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$file=if($Restore){'restore-default-assets.bin'}else{'otto-assets.bin'}
$expected=if($Restore){$manifest.restore_sha256}else{$manifest.sha256}
$image=Join-Path $PSScriptRoot $file
if ((Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Resource checksum mismatch.' }
if ((Get-Item -LiteralPath $image).Length -gt 0x800000) { throw 'Resource partition overflow.' }
& $python -m esptool --chip esp32s3 --port $Port --baud 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_freq 80m --flash_size 16MB 0x800000 $image
if ($LASTEXITCODE -ne 0) { throw "Resource flashing failed: $LASTEXITCODE" }
''', encoding='utf-8')
    (OUT / 'README.md').write_text('''# Otto 动态表情资源包

适用：已烧录本项目 v2.2.4 bread-compact-wifi-lcd 的 ESP32-S3 N16R8，ST7789 240×240 屏幕。

- 21 种 Otto 动态表情，适配为 128×128，保留状态栏和对话文字，黑底白字。
- 保留原资源包中的字体和“你好小智”唤醒模型；仅写入 assets 分区，地址 **0x800000**。
- 不替换应用固件，不写入配网、设备标识所在的 NVS 分区。
- `preview-neutral.gif` 可在浏览器预览待机动画。

在 PowerShell 中执行（关闭占用 COM3 的串口监视器）：

```powershell
.\\artifacts\\otto-st7789-v2.2.4\\flash-otto.ps1 -Port COM3
```

恢复原默认表情：

```powershell
.\\artifacts\\otto-st7789-v2.2.4\\flash-otto.ps1 -Port COM3 -Restore
```

资源来自 https://github.com/txp666/otto-emoji-gif-component ，MIT 许可见 OTTO-LICENSE.txt。
完整校验与来源见 manifest.json。资源文件不能按完整固件的 0x0 地址烧录。
''', encoding='utf-8')
    sums = '\n'.join(f'{sha(p.read_bytes())}  {p.name}' for p in sorted(OUT.iterdir())
                     if p.is_file() and p.name != 'SHA256SUMS.txt') + '\n'
    (OUT / 'SHA256SUMS.txt').write_text(sums, encoding='utf-8')
    package = OUT.parent / (OUT.name + '.zip')
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            archive.write(path, path.name)
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
    print(json.dumps({'assets_bytes': dest.stat().st_size, 'animations': len(entries),
                      'preserved_files': list(kept), 'sha256': manifest['sha256'],
                      'package': str(package)}, indent=2))


if __name__ == '__main__':
    main()
