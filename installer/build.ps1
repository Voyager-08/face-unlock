param(
    [string]$Source = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$Work = $PSScriptRoot,
    [string]$Iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)
$ErrorActionPreference = 'Stop'

$stage = Join-Path $Work 'stage'
$out = Join-Path $Work 'output'
Write-Host "staging from $Source"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $out | Out-Null

foreach ($d in 'src', 'models', 'scripts') {
    Copy-Item (Join-Path $Source $d) (Join-Path $stage $d) -Recurse -Force
}

$dll = Join-Path $Source 'cp\FaceUnlockCP.dll'
if (-not (Test-Path $dll)) {
    Write-Host 'building credential provider'
    & (Join-Path $Source 'cp\build.bat')
}
New-Item -ItemType Directory -Force (Join-Path $stage 'cp') | Out-Null
foreach ($f in 'FaceUnlockCP.dll', 'FaceUnlockCP.cpp', 'FaceUnlockCP.def', 'build.bat') {
    Copy-Item (Join-Path $Source "cp\$f") (Join-Path $stage "cp\$f") -Force
}

$venv = Join-Path $Source '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    throw 'venv not found. Create it first (see README).'
}
Write-Host 'copying venv'
$rc = Start-Process -FilePath 'robocopy.exe' -ArgumentList @(
    "`"$venv`"", "`"$stage\.venv`"", '/E', '/XD', '__pycache__', '/XF', '*.pyc',
    '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -Wait -PassThru -NoNewWindow
if ($rc.ExitCode -ge 8) { throw "robocopy failed: $($rc.ExitCode)" }

$py = Join-Path $venv 'Scripts\python.exe'
$iconScript = Join-Path $Work 'make_icon.py'
@'
import sys
from PIL import Image, ImageDraw

def face(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64.0
    d.ellipse((4*s, 4*s, 60*s, 60*s), fill=(255, 203, 50, 255),
              outline=(224, 168, 20, 255), width=max(1, int(2*s)))
    d.ellipse((19*s, 23*s, 27*s, 34*s), fill=(70, 50, 10, 255))
    d.ellipse((37*s, 23*s, 45*s, 34*s), fill=(70, 50, 10, 255))
    d.arc((17*s, 28*s, 47*s, 52*s), start=15, end=165,
          fill=(70, 50, 10, 255), width=max(1, int(4*s)))
    d.ellipse((12*s, 10*s, 22*s, 18*s), fill=(255, 245, 200, 170))
    return img

face(256).save(sys.argv[1], format="ICO",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
'@ | Set-Content -Path $iconScript -Encoding utf8
& $py $iconScript (Join-Path $Work 'face.ico')
Copy-Item (Join-Path $Work 'face.ico') (Join-Path $stage 'face.ico') -Force

$env:FACEUNLOCK_STAGE = $stage
$env:FACEUNLOCK_WORK = $Work
Write-Host 'compiling installer'
& $Iscc (Join-Path $Work 'FaceUnlock.iss')
Write-Host "output: $out"
