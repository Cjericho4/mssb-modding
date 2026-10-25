# Saves the Clone Maker window as $env:SHOT_DIR\<name>.png (called by shot.cmd during --selftest)
param([string]$Name)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win {
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
}
"@
[Win]::SetProcessDPIAware() | Out-Null
$r = New-Object Win+Rect
[Win]::GetWindowRect([Win]::FindWindow($null, "MSSB Clone Maker"), [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.Right - $r.Left), ($r.Bottom - $r.Top)
[System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save((Join-Path $env:SHOT_DIR "$Name.png"))
