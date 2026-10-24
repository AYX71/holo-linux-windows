$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class HoloDpi {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
'@
[void][HoloDpi]::SetProcessDPIAware()
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$folder = Join-Path ([Environment]::GetFolderPath("MyPictures")) "Screenshots"
[void][IO.Directory]::CreateDirectory($folder)
$file = Join-Path $folder ("holo-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff") + ".png")
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bitmap.Save($file, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
    }
} finally {
    $bitmap.Dispose()
}
& (Join-Path $PSScriptRoot "holo-notify.ps1") -Message ("Screenshot saved: " + $file)
