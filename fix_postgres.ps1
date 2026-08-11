$ErrorActionPreference = "Stop"

$pgRoot = "C:\Program Files\PostgreSQL\16"
$dataDir = Join-Path $pgRoot "data"
$binDir = Join-Path $pgRoot "bin"
$hbaPath = Join-Path $dataDir "pg_hba.conf"
$backupPath = Join-Path $dataDir "pg_hba.conf.ultron-backup"
$psql = Join-Path $binDir "psql.exe"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from PowerShell as Administrator."
}

Copy-Item -LiteralPath $hbaPath -Destination $backupPath -Force

$content = Get-Content -Raw -LiteralPath $hbaPath
$content = $content -replace 'host\s+all\s+all\s+127\.0\.0\.1/32\s+scram-sha-256', 'host    all             all             127.0.0.1/32            trust'
$content = $content -replace 'host\s+all\s+all\s+::1/128\s+scram-sha-256', 'host    all             all             ::1/128                 trust'
Set-Content -LiteralPath $hbaPath -Value $content -Encoding ASCII

Restart-Service -Name "postgresql-x64-16" -Force

& $psql -h 127.0.0.1 -U postgres -d postgres -c "DO `$`$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ultron_user') THEN CREATE ROLE ultron_user LOGIN PASSWORD 'ultron_password'; ELSE ALTER ROLE ultron_user WITH LOGIN PASSWORD 'ultron_password'; END IF; END `$`$;"

$dbExists = & $psql -h 127.0.0.1 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'ultron';"
if ($dbExists.Trim() -ne "1") {
    & $psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE ultron OWNER ultron_user;"
}

& $psql -h 127.0.0.1 -U postgres -d postgres -c "ALTER DATABASE ultron OWNER TO ultron_user;"
& $psql -h 127.0.0.1 -U postgres -d ultron -c "GRANT ALL PRIVILEGES ON SCHEMA public TO ultron_user;"

Copy-Item -LiteralPath $backupPath -Destination $hbaPath -Force
Restart-Service -Name "postgresql-x64-16" -Force

$env:PGPASSWORD = "ultron_password"
& $psql -h 127.0.0.1 -U ultron_user -d ultron -c "SELECT current_database(), current_user;"

Write-Host "PostgreSQL is ready for ULTRON."
