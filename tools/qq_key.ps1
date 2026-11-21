# Keep a QQNT database key in a DPAPI-protected file (Windows) and load it into an environment variable.
# The file can only be decrypted by the same Windows user on the same computer. The key never appears on the
# command line or in the shell history.
#
#   . .\tools\qq_key.ps1                          # dot-source to get the two functions
#   Save-QQKey -Path "$env:LOCALAPPDATA\QQKeys\main.dpapi"      # asks for the key, input is hidden
#   Set-QQKeyEnv -Path "$env:LOCALAPPDATA\QQKeys\main.dpapi" -Name QQ_KEY
#   python .\tools\db_stats.py <nt_msg.db> --key-env QQ_KEY
#   Remove-Item Env:QQ_KEY                         # when done

function Save-QQKey {
    param([Parameter(Mandatory)][string]$Path)
    if (Test-Path -LiteralPath $Path) { throw "$Path already exists" }
    $secure = Read-Host -AsSecureString -Prompt 'Database key'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    Set-Content -LiteralPath $Path -Value (ConvertFrom-SecureString $secure) -Encoding ascii
}

function Set-QQKeyEnv {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
    $secure = ConvertTo-SecureString ([IO.File]::ReadAllText($Path).Trim())
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { Set-Item -Path "Env:$Name" -Value ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
