<#
    Install (or update) Sensor Tracking Studio on this machine.

    Copies the built folder to %USERPROFILE%\SensorTrackingStudio and creates
    Start-menu and Desktop shortcuts carrying the app's own icon.

    Why the user profile and not C:\ or Program Files:
      * managed machines (University of Leeds ones do this) block executables
        run from the root of C:;
      * Program Files is read-only for normal users, and this app writes
        datasets\, config\, outputs\, reports\ and visualizations\ beside its
        .exe.

    Why the profile root and not %LOCALAPPDATA%: those same folders are the
    operator's working data. Recordings come out of outputs\, the raw
    calibration CSVs go into input_raw_data\, and both are reached by hand in
    Explorer. AppData is hidden by default, so the install was invisible to
    the person who has to put files in it.

    Run from the project root, after packaging\build.bat:

        powershell -ExecutionPolicy Bypass -File packaging\install.ps1

    Re-running it upgrades in place: the program files are replaced, and the
    data folders are left alone.

    -Dest <path>     install somewhere else
    -NoDesktop       Start menu only
    -SeedRawData     on a FRESH install only, also copy the checkout's
                     input_raw_data\*.csv. That is gigabytes, and the live
                     application does not need it - only a re-calibration
                     does. The equations file is seeded either way.
    -RefreshIcons    clear Windows' icon cache and restart Explorer. Only
                     needed if the shortcuts still show a previous version's
                     picture; the install already nudges the shell without it.
#>
[CmdletBinding()]
param(
    [string]$Dest = (Join-Path $env:USERPROFILE 'SensorTrackingStudio'),
    [switch]$NoDesktop,
    [switch]$SeedRawData,
    [switch]$RefreshIcons
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $PSScriptRoot
$build   = Join-Path $root 'dist\SensorTrackingStudio'
$exeName = 'SensorTrackingStudio.exe'
$appName = 'Sensor Tracking Studio'
$appDesc = 'Sensor Tracking Studio - 12-channel force array calibration and live monitoring'

if (-not (Test-Path (Join-Path $build $exeName))) {
    throw "No build found at $build`nRun packaging\build.bat first."
}

# Everything the operator accumulates; never overwritten by an upgrade.
$keep = @('input_raw_data', 'datasets', 'config', 'outputs', 'reports', 'visualizations')

$upgrade = Test-Path (Join-Path $Dest $exeName)
Write-Host ("{0} {1}" -f $(if ($upgrade) { 'Upgrading' } else { 'Installing' }), $Dest)

if ($upgrade) {
    # Replace the program, leave the data. _internal is fully rebuilt so a
    # module dropped from the spec does not linger.
    Remove-Item (Join-Path $Dest '_internal') -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $Dest -File | Remove-Item -Force
} else {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
}

Get-ChildItem $build | Where-Object { $keep -notcontains $_.Name } | ForEach-Object {
    Copy-Item $_.FullName -Destination $Dest -Recurse -Force
}

$exe = Join-Path $Dest $exeName

# Shortcuts point at this rather than at the .exe's embedded resource. Windows
# caches icons per source path, so an .exe whose picture changed but whose path
# did not keeps serving the stale bitmap; a file we control lets an upgrade
# swap it, and -RefreshIcons is the hammer for when the cache is stubborn.
$icoSrc = Join-Path $PSScriptRoot 'app_icon.ico'
$ico = Join-Path $Dest 'app_icon.ico'
if (Test-Path $icoSrc) { Copy-Item $icoSrc -Destination $ico -Force } else { $ico = "$exe,0" }

if (-not $upgrade) {
    # The equations are the operator's own data and git-ignored, so they are
    # never in a build; without them a fresh install has no sensors to bind.
    # At ~120 KB this always comes along.
    $eq = Join-Path $root 'datasets\sensor_fit_equations.csv'
    if (Test-Path $eq) {
        New-Item -ItemType Directory -Path (Join-Path $Dest 'datasets') -Force | Out-Null
        Copy-Item $eq -Destination (Join-Path $Dest 'datasets') -Force
        Write-Host '  seeded: datasets\sensor_fit_equations.csv'
    }

    # The raw CSVs are gigabytes and only a re-calibration reads them, so they
    # are copied on request rather than by default.
    if ($SeedRawData) {
        $raw = Join-Path $root 'input_raw_data'
        if (Test-Path $raw) {
            $size = (Get-ChildItem $raw -Filter *.csv | Measure-Object Length -Sum).Sum / 1GB
            Write-Host ("  copying input_raw_data ({0:N1} GB), this takes a while..." -f $size)
            New-Item -ItemType Directory -Path (Join-Path $Dest 'input_raw_data') -Force | Out-Null
            Copy-Item (Join-Path $raw '*.csv') -Destination (Join-Path $Dest 'input_raw_data') -Force
            $n = (Get-ChildItem (Join-Path $Dest 'input_raw_data') -Filter *.csv).Count
            Write-Host "  seeded: input_raw_data\ ($n files)"
        }
    }
}

function New-Shortcut([string]$LinkPath, [string]$Target, [string]$Description, [string]$IconRef) {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($LinkPath)
    $sc.TargetPath       = $Target
    $sc.WorkingDirectory = Split-Path -Parent $Target
    $sc.IconLocation     = $IconRef
    $sc.Description      = $Description
    $sc.Save()
    Write-Host "  shortcut: $LinkPath"
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$iconRef = $(if ($ico -like '*,0') { $ico } else { "$ico,0" })

New-Shortcut (Join-Path $startMenu "$appName.lnk") $exe $appDesc $iconRef

if (-not $NoDesktop) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) "$appName.lnk") $exe $appDesc $iconRef
}

# Tell the shell the icons moved. Cheap and non-disruptive; usually enough.
if (-not ([System.Management.Automation.PSTypeName]'StsShell.Api').Type) {
    Add-Type -Namespace StsShell -Name Api -MemberDefinition @'
[DllImport("shell32.dll", CharSet=CharSet.Auto, SetLastError=true)]
public static extern void SHChangeNotify(int wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
'@
}
[StsShell.Api]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)  # SHCNE_ASSOCCHANGED
& ie4uinit.exe -show 2>$null

if ($RefreshIcons) {
    Write-Host '  clearing the icon cache and restarting Explorer...'
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    Remove-Item (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer\iconcache*.db') `
                -Force -ErrorAction SilentlyContinue
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }
}

Write-Host ''
Write-Host "Done. $exe"
if (-not (Test-Path (Join-Path $Dest 'datasets\sensor_fit_equations.csv'))) {
    Write-Host 'NOTE: no equations yet. Put the raw CSVs in input_raw_data\ next'
    Write-Host '      to the .exe and press RUN CALIBRATION on the Setup page.'
}
