param([string]$InitialPath = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
$dialog.Description = '选择采集资料的保存位置（国内和国外资料分别保存）'
$dialog.ShowNewFolderButton = $true
if (Test-Path -LiteralPath $InitialPath -PathType Container) { $dialog.SelectedPath = $InitialPath }
$owner = [System.Windows.Forms.Form]::new()
$owner.Text = '采集资料保存位置'
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = 'CenterScreen'
$owner.Size = [System.Drawing.Size]::new(1, 1)
$owner.Opacity = 0
try {
    # The API is owned by a background service, so an unowned dialog can appear
    # behind the Edge app. A temporary topmost owner makes this user action visible.
    $owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write($dialog.SelectedPath)
    }
} finally { $dialog.Dispose(); $owner.Dispose() }
