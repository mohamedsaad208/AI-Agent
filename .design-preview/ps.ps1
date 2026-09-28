Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" | Select-Object ProcessId,CommandLine | Format-Table -AutoSize
