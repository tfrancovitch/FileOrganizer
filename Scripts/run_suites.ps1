<#
run_suites.ps1 -- runs the test suites and prints ONE summary table.

Written so a cheap agent (fo-runner, Haiku) can run every suite and report only
counts and failures. Each suite's full output goes to its own log file; the
console shows one line per suite.

  powershell -ExecutionPolicy Bypass -File Scripts\run_suites.ps1                 # standard tier
  powershell -ExecutionPolicy Bypass -File Scripts\run_suites.ps1 -Tier quick
  powershell -ExecutionPolicy Bypass -File Scripts\run_suites.ps1 -Only p3_plan_check,p3_regression
  powershell -ExecutionPolicy Bypass -File Scripts\run_suites.ps1 -List

Tiers
  quick     self_check, p2_regression, every Phase 3 suite except mutation testing, and the Phase 4 journal and lock suites
  standard  quick + p3_mutation_check + p4_mutation_check + b6_regression (once) + p2_dashboard_check (opens the real window)
  full      standard + b6_regression four more times (five in all) + p2_mutation_check + p2_hostile_check
            + p2_acceptance (only with -AcceptProject; otherwise reported SKIPPED)

Exit code: 0 when every suite run exited 0, otherwise 1. Nothing in the tree is changed by this
script; logs go to -LogDir (default %TEMP%\fo_suites\<timestamp>).
#>
param(
    [ValidateSet('quick', 'standard', 'full')] [string] $Tier = 'standard',
    [string[]] $Only,
    [string] $Root = (Split-Path -Parent $PSScriptRoot),
    [string] $Python = 'python',
    [string] $LogDir = (Join-Path $env:TEMP ('fo_suites\' + (Get-Date -Format 'yyyyMMdd_HHmmss'))),
    [string] $AcceptProject,
    [string] $Truth = 'C:\FOTest\GROUND_TRUTH.json',
    [switch] $List
)

$ErrorActionPreference = 'Continue'

$quick = @(
    'self_check', 'p2_regression', 'p3_regression', 'p3_fixture_check', 'p3_lab_harness',
    'p3_plan_check', 'p3_closeout_check', 'p3_encryption_check', 'p3_revalidation_check',
    'p3_metadata_check', 'p3_metadata_view_check', 'p3_refresh_check', 'p3_survey_check',
    'p3_visual_check', 'p3_visual_bulk_check', 'p3_relabel_check', 'p3_dangling_check',
    'p3_registry_schema_check', 'p3_fingerprint_check', 'p3_organize_check',
    'p4_journal_check', 'p4_lock_check'
)
$standard = $quick + @('p3_mutation_check', 'p4_mutation_check', 'b6_regression', 'p2_dashboard_check')
$full = $standard + @('b6_regression', 'b6_regression', 'b6_regression', 'b6_regression',
                      'p2_mutation_check', 'p2_hostile_check', 'p2_acceptance')

switch ($Tier) { 'quick' { $plan = $quick } 'standard' { $plan = $standard } 'full' { $plan = $full } }
# Under -File, "-Only a,b" arrives as one string; split it here.
if ($Only) { $plan = @($Only | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() -replace '\.py$', '' }) }
$label = $Tier; if ($Only) { $label = 'only' }

if ($List) { $plan | ForEach-Object { $_ }; exit 0 }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$rows = @()
$n = 0
Write-Host ("Tier {0}: {1} suites. Logs: {2}" -f $label, $plan.Count, $LogDir)

foreach ($suite in $plan) {
    $n++
    $script = Join-Path $Root ("Scripts\{0}.py" -f $suite)
    $log = Join-Path $LogDir ("{0:D2}_{1}.log" -f $n, $suite)
    $extra = ''
    if ($suite -eq 'p2_acceptance') {
        if (-not $AcceptProject) {
            $rows += [pscustomobject]@{ Suite = $suite; Exit = 'SKIPPED'; Seconds = 0; Result = 'no -AcceptProject given' }
            continue
        }
        $extra = ('--project "{0}" --truth "{1}"' -f $AcceptProject, $Truth)
    }
    if (-not (Test-Path $script)) {
        $rows += [pscustomobject]@{ Suite = $suite; Exit = 'MISSING'; Seconds = 0; Result = $script }
        continue
    }
    $started = Get-Date
    # cmd merges stdout and stderr into the log, so stderr text never becomes a PowerShell error.
    Push-Location $Root
    cmd /c ("`"{0}`" `"{1}`" {2} > `"{3}`" 2>&1" -f $Python, $script, $extra, $log)
    $code = $LASTEXITCODE
    Pop-Location
    $secs = [int]((Get-Date) - $started).TotalSeconds

    $lines = @(Get-Content -Path $log -ErrorAction SilentlyContinue | Where-Object { $_ -match '[A-Za-z0-9]' })
    # Prefer a count line ("138/138"), then a pass/fail sentence, then the last line.
    $summary = $lines | Where-Object { $_ -match '\b\d+\s*/\s*\d+\b' } | Select-Object -Last 1
    if (-not $summary) { $summary = $lines | Where-Object { $_ -match '(?i)\b(all )?pass(ed)?\b|\bfail(ed|ures?)?\b' } | Select-Object -Last 1 }
    if (-not $summary -and $lines.Count) { $summary = $lines[-1] }
    if ($summary) { $summary = $summary.Trim(); if ($summary.Length -gt 110) { $summary = $summary.Substring(0, 110) } }

    $rows += [pscustomobject]@{ Suite = $suite; Exit = $code; Seconds = $secs; Result = $summary }
    Write-Host ("  [{0}/{1}] {2,-26} exit {3,-3} {4,5}s  {5}" -f $n, $plan.Count, $suite, $code, $secs, $summary)
}

$failed = @($rows | Where-Object { $_.Exit -ne 0 -and $_.Exit -ne 'SKIPPED' })
$md = @('| Suite | Exit | Seconds | Last result line |', '|---|---|---|---|')
$md += $rows | ForEach-Object { '| {0} | {1} | {2} | {3} |' -f $_.Suite, $_.Exit, $_.Seconds, ($_.Result -replace '\|', '/') }
$verdict = '{0} run: {1} exited 0, {2} failed or missing, {3} skipped.' -f $rows.Count,
    @($rows | Where-Object { $_.Exit -eq 0 }).Count, $failed.Count, @($rows | Where-Object { $_.Exit -eq 'SKIPPED' }).Count
$md += '', $verdict
Set-Content -Path (Join-Path $LogDir 'SUMMARY.md') -Value $md -Encoding UTF8

Write-Host ''
$md | ForEach-Object { Write-Host $_ }
if ($failed.Count) { exit 1 } else { exit 0 }
