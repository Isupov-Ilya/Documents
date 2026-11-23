param([Parameter(Mandatory=$true)][string]$Exe,[Parameter(Mandatory=$true)][string]$DataDirectory)
$ErrorActionPreference='Stop'
$Exe=(Resolve-Path $Exe).Path
$Python=(Get-Command python).Source
$Root=Split-Path $PSScriptRoot -Parent
$DataDirectory=[IO.Path]::GetFullPath($DataDirectory)
$ReportDirectory=Join-Path $Root 'dist/validation'
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
$originalPath=$env:PATH
$rulePrefix='Biography-'+[guid]::NewGuid().ToString()
try {
    # The installed executable and all workers must run without Python in PATH.
    $env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    & $Exe --version
    if ($LASTEXITCODE -ne 0) { throw 'Installed executable does not launch' }
    & $Exe prepare-assets --data-directory $DataDirectory > (Join-Path $ReportDirectory 'model-preparation.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Frozen model preparation failed' }
    $config=Join-Path $DataDirectory 'settings.json'
    $diarization=Join-Path $DataDirectory 'models/diarization-onnx/config.json'
    $value=Get-Content $diarization -Raw -Encoding UTF8 | ConvertFrom-Json
    $value.num_clusters=2
    [IO.File]::WriteAllText($diarization,($value|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $audio=Join-Path $DataDirectory 'Запись с телефона.m4a'
    & (Join-Path (Split-Path $Exe) 'tools/ffmpeg.exe') -nostdin -v error -y -i (Join-Path $Root 'examples/audio/interview.wav') -c:a aac -b:a 96k $audio
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create M4A test recording' }
    $before=(Get-FileHash $audio -Algorithm SHA256).Hash
    & $Exe network-probe
    if ($LASTEXITCODE -ne 0) { throw 'Network baseline unavailable; firewall test would be inconclusive' }
    Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
    $programs=Get-ChildItem (Split-Path $Exe) -Filter *.exe -Recurse
    foreach($program in $programs) {
        New-NetFirewallRule -DisplayName ($rulePrefix+'-'+$program.Name) -Direction Outbound -Program $program.FullName -Action Block -Profile Any -Enabled True | Out-Null
    }
    & $Exe network-probe
    if ($LASTEXITCODE -ne 4) { throw 'Windows firewall did not block the application' }
    $project=Join-Path $DataDirectory 'Полная офлайн проверка'
    $profile=Join-Path $DataDirectory 'test-profile/profile.json'
    & $Exe run $audio --config $config --output $project --template-profile $profile > (Join-Path $ReportDirectory 'processing.log') 2>&1
    $exitCode=$LASTEXITCODE
    $automaticRoles=$true
    if ($exitCode -eq 3) {
        # Explicit simulation of the permitted human role-confirmation step.
        $automaticRoles=$false
        $roles=Join-Path $DataDirectory 'confirmed-test-roles.json'
        $env:PATH=$originalPath
        & $Python (Join-Path $PSScriptRoot 'role_truth_for_test.py') --project $project --reference (Join-Path $Root 'examples/audio/reference.json') --output $roles
        $env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
        $source=Join-Path $project 'transcript.json'
        $project=Join-Path $DataDirectory 'Проверка после уточнения ролей'
        & $Exe analyze $source --config $config --roles $roles --output $project --template-profile $profile >> (Join-Path $ReportDirectory 'processing.log') 2>&1
        $exitCode=$LASTEXITCODE
    }
    if ($exitCode -ne 0) { throw "Actual offline pipeline failed: $exitCode" }
    if ((Get-FileHash $audio -Algorithm SHA256).Hash -ne $before) { throw 'Source audio was modified' }
    $env:PATH=$originalPath
    & $Python (Join-Path $PSScriptRoot 'validate_windows_output.py') --project $project --report (Join-Path $ReportDirectory 'offline-test.json')
    if ($LASTEXITCODE -ne 0) { throw 'Frozen output validation failed' }
    $env:QT_QPA_PLATFORM='windows'
    $env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    & $Exe gui-smoke --data-directory $DataDirectory --project $project --output (Join-Path $ReportDirectory 'native-windows-gui.png') > (Join-Path $ReportDirectory 'gui.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Native frozen Windows GUI/media test failed' }
    $proof=@{outbound_firewall_block_verified=$true; network_positive_control=$true; python_removed_from_path=$true; source_audio_unchanged=$true; automatic_roles=$automaticRoles; windows=$PSVersionTable.OS; executable=$Exe}
    [IO.File]::WriteAllText((Join-Path $ReportDirectory 'firewall-proof.json'),($proof|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
} finally {
    $env:PATH=$originalPath
    Get-NetFirewallRule -DisplayName ($rulePrefix+'*') -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
}
