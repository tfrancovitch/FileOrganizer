<#
fo_release.ps1 -- releases one approved build: verify, commit, tag, move the integration branch,
sync the workspace-root docs, push. Run it ONLY after Thomas has approved the build's handback.

Replaces Phase 3's CLOSE_PHASE3.ps1 and fixes what went wrong with it on 2026-11-01:
  1. it never names a function "Git" (that hid git.exe and recursed forever);
  2. git's normal progress text on stderr no longer aborts the script mid-push;
  3. the archive's index is ARCHIVE_INDEX.md, so it can no longer overwrite the archived README.md.

  # look first: checks everything, changes nothing
  powershell -ExecutionPolicy Bypass -File Scripts\fo_release.ps1 -Build B21 -Message "B21: the action lock" -DryRun
  # then for real (from the build's worktree, or pass -Worktree)
  powershell -ExecutionPolicy Bypass -File Scripts\fo_release.ps1 -Build B21 -Message "B21: the action lock"

Steps (it stops at the first problem; nothing after that point is done):
  1. preconditions: a git worktree; the tag is new (here and on the remote); the integration branch can be
     fast-forwarded to this build; Scripts\Test-Installation.ps1 prints "manifest verified";
     nothing under Projects\ or Logs\ and no database file would be committed
  2. commit everything in the worktree (skipped if there is nothing to commit), then an annotated tag
  3. fast-forward the integration branch (default phase4) to the tag; never a merge commit, never forced
  4. sync PROJECT_PLAN.md / README.md / STAGE_MAP.md to the workspace root from the tag, after copying the
     current root copies to _Archive\<date>_root_docs_before_<tag>\ (refuses if that folder exists)
  5. push the build branch, the integration branch and the tag; never forced
Every line goes to <WorkspaceRoot>\ReleaseLogs\<tag>[_dryrun].log
#>
param(
    [Parameter(Mandatory = $true)] [string] $Build,
    [Parameter(Mandatory = $true)] [string] $Message,
    [string] $Worktree = (Get-Location).Path,
    [string] $Tag,
    [string] $Integration = 'phase4',
    [string] $WorkspaceRoot = 'C:\FileOrganizerTesting',
    [string] $Remote = 'origin',
    [string] $Trailer = '',
    [switch] $NoRootSync,
    [switch] $NoPush,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $Tag) { $Tag = 'phase4-' + $Build.ToLower() }
$Docs = @('PROJECT_PLAN.md', 'README.md', 'STAGE_MAP.md')
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$LogDir = Join-Path $WorkspaceRoot 'ReleaseLogs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$suffix = ''; if ($DryRun) { $suffix = '_dryrun' }
$Log = Join-Path $LogDir ("{0}{1}.log" -f $Tag, $suffix)
Set-Content -Path $Log -Value ("fo_release log -- {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8

function Say([string] $msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding UTF8
}
function Stop-Here([string] $msg) { Say "STOPPED: $msg"; Say 'Nothing after this point was done.'; exit 1 }

# Runs git.exe (never a function) and returns its output as strings. stderr is captured as text, not
# raised as an error, so git's progress messages cannot abort the script. Exit codes are checked here.
function Invoke-GitExe {
    param([string[]] $GitArgs, [switch] $AllowFail, [switch] $Quiet)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & git.exe -C $Worktree @GitArgs 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if (-not $Quiet) { $out | ForEach-Object { Say "  git: $_" } }
    if ($code -ne 0 -and -not $AllowFail) { Stop-Here ("git {0} failed (exit {1})" -f ($GitArgs -join ' '), $code) }
    return , @($out)
}
function Sha([string] $path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash }

Say ("Build {0}, tag {1}, worktree {2}, integration {3}, remote {4}{5}" -f $Build, $Tag, $Worktree, $Integration, $Remote, $(if ($DryRun) { ' -- DRY RUN' } else { '' }))

# ---- 1. preconditions -------------------------------------------------------------------------------
Say 'Step 1: preconditions'
if (-not (Test-Path (Join-Path $Worktree '.git'))) { Stop-Here "$Worktree is not a git worktree" }
$branch = (Invoke-GitExe @('rev-parse', '--abbrev-ref', 'HEAD') -Quiet)[0].Trim()
if ($branch -eq 'HEAD') { Stop-Here 'detached HEAD; check out the build branch first' }
if ($branch -eq $Integration) { Stop-Here "you are on $Integration itself; release from the build branch" }
Say "  build branch: $branch"

$local = Invoke-GitExe @('tag', '--list', $Tag) -Quiet
if ($local -and ($local -join '').Trim()) { Stop-Here "tag $Tag already exists here; not overwriting it" }
if (-not $NoPush) {
    $remoteTag = Invoke-GitExe @('ls-remote', '--tags', $Remote, "refs/tags/$Tag") -Quiet
    if (($remoteTag -join '').Trim()) { Stop-Here "tag $Tag already exists on $Remote" }
}
Invoke-GitExe @('rev-parse', '--verify', "refs/heads/$Integration") -Quiet | Out-Null
$anc = Invoke-GitExe @('merge-base', '--is-ancestor', "refs/heads/$Integration", 'HEAD') -AllowFail -Quiet
if ($LASTEXITCODE -ne 0) { Stop-Here "$Integration has commits this build does not; it cannot be fast-forwarded. Ask Thomas how to merge." }
Say "  $Integration can be fast-forwarded to this build"

$ti = Join-Path $Worktree 'Scripts\Test-Installation.ps1'
if (-not (Test-Path $ti)) { Stop-Here "missing $ti" }
$tiOut = & cmd /c ("powershell -NoProfile -ExecutionPolicy Bypass -File `"{0}`" 2>&1" -f $ti)
$tiOut | Select-Object -Last 3 | ForEach-Object { Say "  $_" }
if (-not ($tiOut -match 'manifest verified')) { Stop-Here 'Test-Installation.ps1 did not report "manifest verified"' }

$status = Invoke-GitExe @('status', '--porcelain', '--untracked-files=all') -Quiet
$status | ForEach-Object { Say "  $_" }
$bad = $status | Where-Object { ($_ -match '(^|[ /\\])(Projects|Logs)[/\\]') -or (($_ -match '\.(sqlite|db|db-wal|db-shm)$') -and ($_ -notmatch 'fixture_lab')) }
if ($bad) { Stop-Here ("these paths must not be committed: {0}" -f ($bad -join '; ')) }
$dirty = [bool](($status -join '').Trim())

if ($DryRun) {
    Say 'DRY RUN: every check passed. A real run would:'
    if ($dirty) { Say ("  commit {0} changed path(s) on {1}: {2}" -f @($status).Count, $branch, $Message) } else { Say '  commit nothing (tree clean)' }
    Say "  create annotated tag $Tag; fast-forward $Integration to it"
    if (-not $NoRootSync) { Say "  sync $($Docs -join ', ') to $WorkspaceRoot from $Tag, archiving the current copies" }
    if (-not $NoPush) { Say "  push $branch, $Integration and $Tag to $Remote (no force)" }
    exit 0
}

# ---- 2. commit and tag ------------------------------------------------------------------------------
Say 'Step 2: commit and tag'
if ($dirty) {
    Invoke-GitExe @('add', '-A') | Out-Null
    $msgArgs = @('commit', '-m', $Message)
    if ($Trailer) { $msgArgs += @('-m', $Trailer) }
    Invoke-GitExe $msgArgs | Out-Null
} else { Say '  nothing to commit; tagging HEAD' }
Invoke-GitExe @('tag', '-a', $Tag, '-m', ("{0}: {1}" -f $Build, $Message)) | Out-Null
$commit = (Invoke-GitExe @('rev-parse', "refs/tags/$Tag^{commit}") -Quiet)[0].Trim()
Say "  commit $commit, tag $Tag"

# ---- 3. fast-forward the integration branch ---------------------------------------------------------
Say "Step 3: fast-forward $Integration"
$wtList = Invoke-GitExe @('worktree', 'list', '--porcelain') -Quiet
$intPath = $null; $cur = $null
foreach ($l in $wtList) {
    if ($l -like 'worktree *') { $cur = $l.Substring(9) }
    if ($l -eq "branch refs/heads/$Integration") { $intPath = $cur }
}
if ($intPath) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $o = & git.exe -C $intPath merge --ff-only "refs/tags/$Tag" 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE; $ErrorActionPreference = $old
    $o | ForEach-Object { Say "  git: $_" }
    if ($code -ne 0) { Stop-Here "fast-forward of $Integration in $intPath failed (is that worktree dirty?)" }
} else {
    $before = (Invoke-GitExe @('rev-parse', "refs/heads/$Integration") -Quiet)[0].Trim()
    Invoke-GitExe @('update-ref', "refs/heads/$Integration", $commit, $before) | Out-Null
}
Say "  $Integration is now $commit"

# ---- 4. root sync ------------------------------------------------------------------------------------
if (-not $NoRootSync) {
    Say "Step 4: root sync from $Tag"
    $archive = Join-Path $WorkspaceRoot ("_Archive\{0}_root_docs_before_{1}" -f (Get-Date -Format 'yyyy-MM-dd'), $Tag)
    if (Test-Path $archive) { Stop-Here "archive folder $archive already exists; not overwriting it" }
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $rows = @()
    foreach ($d in $Docs) {
        $rootPath = Join-Path $WorkspaceRoot $d
        $before = '(none)'
        if (Test-Path $rootPath) { $before = Sha $rootPath; Copy-Item -Path $rootPath -Destination (Join-Path $archive $d) }
        # cmd redirection writes git's bytes unchanged; PowerShell redirection would re-encode them.
        cmd /c ("git.exe -C `"{0}`" show `"refs/tags/{1}:{2}`" > `"{3}`"" -f $Worktree, $Tag, $d, $rootPath)
        if ($LASTEXITCODE -ne 0) { Stop-Here "could not write $d from $Tag" }
        $rows += , @($d, $before, (Sha $rootPath))
    }
    $planPath = Join-Path $WorkspaceRoot 'PROJECT_PLAN.md'
    $text = [IO.File]::ReadAllText($planPath, $Utf8)
    $i = $text.IndexOf("`n`n")
    if ($i -lt 0) { Stop-Here 'PROJECT_PLAN.md has no blank line after its title' }
    $note = ("Synced from tag {0} on {1}. The workspace root copy is canonical; each round syncs it from the last tagged build." -f $Tag, (Get-Date -Format 'yyyy-MM-dd'))
    [IO.File]::WriteAllText($planPath, ($text.Substring(0, $i) + "`n`n" + $note + $text.Substring($i)), $Utf8)
    $index = @("# Root docs before $Tag", '', "The workspace-root copies replaced by the copies from tag ``$Tag``. Copied here, not deleted.", '',
               '| File | SHA-256 (archived) | SHA-256 (from the tag) |', '|---|---|---|')
    foreach ($r in $rows) { $index += ('| `{0}` | `{1}` | `{2}` |' -f $r[0], $r[1], $r[2]); Say ("  {0}: {1} -> {2}" -f $r[0], $r[1], $r[2]) }
    [IO.File]::WriteAllText((Join-Path $archive 'ARCHIVE_INDEX.md'), (($index -join "`n") + "`n"), $Utf8)
    Say ("  PROJECT_PLAN.md at the root, with the sync line: {0}" -f (Sha $planPath))
} else { Say 'Step 4: root sync skipped (-NoRootSync)' }

# ---- 5. push -----------------------------------------------------------------------------------------
if (-not $NoPush) {
    Say "Step 5: push to $Remote (no force)"
    Invoke-GitExe @('push', $Remote, "refs/heads/${branch}:refs/heads/$branch", "refs/heads/${Integration}:refs/heads/$Integration", "refs/tags/${Tag}:refs/tags/$Tag") | Out-Null
    $check = Invoke-GitExe @('ls-remote', $Remote, "refs/heads/$Integration", "refs/tags/$Tag") -Quiet
    $check | ForEach-Object { Say "  remote: $_" }
    if (-not (($check -join ' ') -match "$commit\s+refs/heads/$Integration")) { Stop-Here "the remote's $Integration is not $commit after the push" }
    if (-not (($check -join ' ') -match "refs/tags/$Tag")) { Stop-Here "the remote does not show tag $Tag after the push" }
} else { Say 'Step 5: push skipped (-NoPush)' }

Say "DONE: $Build released as $Tag ($commit)."
