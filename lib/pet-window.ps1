# =============================================================================
#  Dota 2 Pet - standalone desktop window (Windows only)
#
#  Spawned by the plugin host half via powershell.exe. Creates a borderless,
#  truly transparent, always-on-top WPF window that plays baked action frames.
#  It stays on the desktop even when DSH is minimised.
#
#  NOTE: every string in this file is intentionally ASCII. Windows PowerShell
#  5.1 reads .ps1 as ANSI unless the file has a UTF-8 BOM, so non-ASCII text
#  here would corrupt the parser. The file is also saved WITH a BOM.
#
#  Usage (the host builds these args; you can also run it by hand):
#    powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden `
#        -File pet-window.ps1 -Frames "C:\...\assets\frames\slark" -Action idle_anim
# =============================================================================

param(
  [Parameter(Mandatory = $true)][string]$Frames,
  [string]$Action = 'idle_anim',
  [double]$Scale = 1.0,
  [int]$Left = 0,
  [int]$Top = 0,
  [int]$Fps = 0,
  [string]$LogFile = '',
  [string]$Labels = ''
)

$ErrorActionPreference = 'Stop'

if (-not $LogFile -or $LogFile -eq '') {
  $LogFile = Join-Path $Frames '_window.log'
}

function Write-Log([string]$msg) {
  try {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
  } catch { }
}

# Chinese labels come from a UTF-8 JSON file written by the plugin host half, so this
# script stays pure ASCII on purpose: Windows PowerShell 5.1 decodes a .ps1 without a
# BOM as ANSI, and non-ASCII text in the source would break the parser (it did once).
$L = @{}
if ($Labels -and (Test-Path $Labels)) {
  try {
    $j = Get-Content $Labels -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $j.PSObject.Properties) { $L[$p.Name] = [string]$p.Value }
    Write-Log ('labels loaded: ' + $L.Count)
  } catch {
    Write-Log ('labels load failed: ' + $_.Exception.Message)
  }
}
function T([string]$key, [string]$fallback) {
  if ($L.ContainsKey($key)) { return $L[$key] }
  return $fallback
}

Write-Log ('=== start pid=' + $PID + ' action=' + $Action + ' scale=' + $Scale + ' ===')
Write-Log ('frames dir: ' + $Frames)

try {
  Add-Type -AssemblyName PresentationFramework
  Add-Type -AssemblyName PresentationCore
  Add-Type -AssemblyName WindowsBase
} catch {
  Write-Log ('FATAL cannot load WPF: ' + $_.Exception.Message)
  exit 1
}

$manifestPath = Join-Path $Frames 'manifest.json'
if (-not (Test-Path $manifestPath)) {
  Write-Log ('FATAL manifest.json not found: ' + $manifestPath)
  exit 2
}

try {
  $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
  Write-Log ('FATAL manifest.json parse error: ' + $_.Exception.Message)
  exit 3
}

function Load-Frames([object]$clip) {
  $list = New-Object 'System.Collections.Generic.List[object]'
  foreach ($f in $clip.Files) {
    $p = Join-Path $Frames $f
    if (-not (Test-Path $p)) { continue }
    try {
      $bi = New-Object System.Windows.Media.Imaging.BitmapImage
      $bi.BeginInit()
      $bi.UriSource = New-Object System.Uri($p)
      $bi.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
      $bi.EndInit()
      $bi.Freeze()
      $list.Add($bi)
    } catch {
      Write-Log ('warn: cannot load ' + $f + ' : ' + $_.Exception.Message)
    }
  }
  return $list
}

function Get-Clip([string]$name) {
  if (-not $name) { return $null }
  $a = $manifest.actions.$name
  if (-not $a) { return $null }
  # Pick the yaw closest to the front (smallest absolute yaw). Taking yaws[0] used to
  # select -180 (the back of the head), so the pet showed its back all the time.
  $yaw = $null
  foreach ($y in $a.yaws) {
    if (-not $y.files) { continue }
    if (-not $yaw) { $yaw = $y; continue }
    if ([Math]::Abs([double]$y.yaw) -lt [Math]::Abs([double]$yaw.yaw)) { $yaw = $y }
  }
  if (-not $yaw -or -not $yaw.files) { return $null }
  $fps = 12.0
  if ($a.fps) { $fps = [double]$a.fps }
  return [pscustomobject]@{
    Name  = $name
    Files = @($yaw.files)
    Fps   = $fps
    Loop  = [bool]$a.loop
  }
}

$clip = Get-Clip $Action
if (-not $clip) { $clip = Get-Clip 'idle_anim' }
if (-not $clip) {
  $first = $manifest.actions.PSObject.Properties | Select-Object -First 1
  if ($first) { $clip = Get-Clip $first.Name }
}
if (-not $clip) {
  Write-Log 'FATAL no playable action in manifest'
  exit 4
}

$bitmaps = Load-Frames $clip
if ($bitmaps.Count -eq 0) {
  Write-Log 'FATAL no frame loaded'
  exit 5
}
Write-Log ('clip=' + $clip.Name + ' frames=' + $bitmaps.Count + ' fps=' + $clip.Fps + ' loop=' + $clip.Loop)

$size = 220.0 * $Scale
if ($manifest.size) { $size = [double]$manifest.size * $Scale }

$window = New-Object System.Windows.Window
$window.WindowStyle = [System.Windows.WindowStyle]::None
$window.AllowsTransparency = $true
$window.Background = [System.Windows.Media.Brushes]::Transparent
$window.Topmost = $true
$window.ShowInTaskbar = $false
$window.ResizeMode = [System.Windows.ResizeMode]::NoResize
$window.WindowStartupLocation = [System.Windows.WindowStartupLocation]::Manual
$window.Width = $size
$window.Height = $size
$wa = [System.Windows.SystemParameters]::WorkArea
if ($Left -gt 0) { $window.Left = $Left } else { $window.Left = $wa.Right - $size - 24 }
if ($Top -gt 0) { $window.Top = $Top } else { $window.Top = $wa.Bottom - $size - 24 }

$image = New-Object System.Windows.Controls.Image
$image.Stretch = [System.Windows.Media.Stretch]::Uniform
$image.Source = $bitmaps[0]
$window.Content = $image

# left drag moves the window
$window.Add_MouseLeftButtonDown({ try { $window.DragMove() } catch { } })

# right click menu: action list is built from this hero's manifest (action names differ
# per hero), plus scale / quit. categoryEn keeps the labels ASCII.
$menu = New-Object System.Windows.Controls.ContextMenu
$actionNames = @()
foreach ($prop in $manifest.actions.PSObject.Properties) {
  if ($prop.Name.StartsWith('@@')) { continue }
  $actionNames += $prop.Name
}
Write-Log ('actions in manifest: ' + ($actionNames -join ', '))
foreach ($name in $actionNames) {
  $entry = $manifest.actions.$name
  $cat = 'action'
  if ($entry.categoryEn) { $cat = $entry.categoryEn }
  $mi = New-Object System.Windows.Controls.MenuItem
  $mi.Header = (T $cat $cat) + '  ' + $name
  $mi.Tag = $name
  $mi.Add_Click({
    param($sender, $evt)
    $newClip = Get-Clip $sender.Tag
    if ($newClip) {
      $new = Load-Frames $newClip
      if ($new.Count -gt 0) {
        $bitmaps.Clear()
        foreach ($b in $new) { $bitmaps.Add($b) }
        $script:clip = $newClip
        $script:index = 0
        Write-Log ('switch action -> ' + $sender.Tag + ' frames=' + $bitmaps.Count)
      }
    }
  })
  Write-Log ('menu item: ' + $mi.Header)
  $menu.Items.Add($mi) | Out-Null
}
$miBig = New-Object System.Windows.Controls.MenuItem
$miBig.Header = (T 'scaleUp' 'Scale +')
$miBig.Add_Click({ $window.Width = $window.Width * 1.15; $window.Height = $window.Height * 1.15 })
$menu.Items.Add($miBig) | Out-Null
$miSmall = New-Object System.Windows.Controls.MenuItem
$miSmall.Header = (T 'scaleDown' 'Scale -')
$miSmall.Add_Click({ $window.Width = $window.Width / 1.15; $window.Height = $window.Height / 1.15 })
$menu.Items.Add($miSmall) | Out-Null
$miQuit = New-Object System.Windows.Controls.MenuItem
$miQuit.Header = (T 'quit' 'Quit pet')
$miQuit.Add_Click({ Write-Log 'quit from menu'; $window.Close() })
$menu.Items.Add($miQuit) | Out-Null
$window.ContextMenu = $menu

$script:clip = $clip
$script:index = 0
$playFps = $clip.Fps
if ($Fps -gt 0) { $playFps = $Fps }
if ($playFps -lt 6) { $playFps = 6 }
if ($playFps -gt 24) { $playFps = 24 }
$interval = [int](1000 / $playFps)
if ($interval -lt 33) { $interval = 33 }

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds($interval)
$timer.Add_Tick({
  if ($bitmaps.Count -eq 0) { return }
  $script:index++
  if ($script:index -ge $bitmaps.Count) {
    if ($script:clip.Loop) { $script:index = 0 } else { $script:index = $bitmaps.Count - 1 }
  }
  $image.Source = $bitmaps[$script:index]
})
$timer.Start()

$script:beats = 0
$hb = New-Object System.Windows.Threading.DispatcherTimer
$hb.Interval = [TimeSpan]::FromSeconds(15)
$hb.Add_Tick({
  $script:beats++
  Write-Log ('alive beats=' + $script:beats + ' frames=' + $bitmaps.Count + ' size=' + [int]$window.Width + ' pos=' + [int]$window.Left + ',' + [int]$window.Top)
})
$hb.Start()

$window.Add_SourceInitialized({
  try {
    $helper = New-Object System.Windows.Interop.WindowInteropHelper($window)
    Write-Log ('window ready hwnd=' + $helper.Handle + ' size=' + [int]$window.Width + 'x' + [int]$window.Height + ' pos=' + [int]$window.Left + ',' + [int]$window.Top)
  } catch {
    Write-Log ('window ready (handle unknown): ' + $_.Exception.Message)
  }
})
$window.Add_Closed({ Write-Log 'closed'; $timer.Stop(); $hb.Stop() })

try {
  $app = New-Object System.Windows.Application
  $app.Run($window) | Out-Null
  Write-Log '=== exit 0 ==='
} catch {
  Write-Log ('FATAL run failed: ' + $_.Exception.Message)
  exit 6
}