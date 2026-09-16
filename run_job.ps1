param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromTask
)

Set-Location -LiteralPath $PSScriptRoot
& "C:\Users\yp8700\anaconda3\python.exe" ".\main.py" @ArgsFromTask
exit $LASTEXITCODE
