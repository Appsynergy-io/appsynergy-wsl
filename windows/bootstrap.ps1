# Run from PowerShell 7.4+: irm https://raw.githubusercontent.com/Appsynergy-io/appsynergy-wsl/main/windows/bootstrap.ps1 | iex
# Scope the bootstrap so it does not change the caller's preferences or functions.
& {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    if ($PSVersionTable.PSVersion -lt [version]'7.4' -or [Environment]::OSVersion.Platform -ne 'Win32NT') {
        throw 'Open PowerShell 7.4+ on Windows 11 and run the same installation command. Windows prerequisites are documented in README.md.'
    }

    function Receive-BootstrapVerifier {
        param([string]$Path)
        $uri = 'https://raw.githubusercontent.com/Appsynergy-io/appsynergy-wsl/f9cb2604168fa28121721f3b7296035c1c1d60ae/windows/AppSynergy.psm1'
        $client = [Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(60)
        try {
            $client.MaxResponseContentBufferSize = 1MB
            $bytes = $client.GetByteArrayAsync($uri).GetAwaiter().GetResult()
            $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
            if ($hash -cne '44feedbf409b404e9a297adb4117836601ff4c712f6f93a5d1a3db8765836834') {
                throw 'Bootstrap verifier hash mismatch'
            }
            [IO.File]::WriteAllBytes($Path, $bytes)
        } finally { $client.Dispose() }
    }

    function Expand-VerifiedToolArchive {
        param([string]$Archive, [hashtable]$Entry, [string]$Destination)
        if ($Entry.size -le 0 -or $Entry.size -gt 16MB -or
            (Get-Item -LiteralPath $Archive).Length -ne $Entry.size -or
            (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Entry.sha256) {
            throw 'Installer archive hash or size mismatch'
        }
        $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $allowed = @('LICENSE', 'windows/AppSynergy.ps1', 'windows/AppSynergy.psm1',
                         'windows/Install.ps1', 'windows/Acceptance.ps1', 'windows/bootstrap.ps1', 'windows/trust/release.pem')
            $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            $total = 0L
            foreach ($file in $zip.Entries) {
                $total += $file.Length
                if ($file.FullName -cnotin $allowed -or -not $names.Add($file.FullName) -or
                    $file.Length -gt 4MB -or $total -gt 16MB) { throw 'Unsafe installer archive contents' }
            }
            foreach ($required in @('windows/Install.ps1', 'windows/AppSynergy.ps1', 'windows/AppSynergy.psm1', 'windows/trust/release.pem')) {
                if (-not $names.Contains($required)) { throw "Missing installer file: $required" }
            }
            # A fresh destination and explicit regular-file writes prevent ZIP links,
            # traversal and overwrites. Inspect every entry before writing any entry.
            $null = New-Item -ItemType Directory -Path $Destination
            foreach ($file in $zip.Entries) {
                $target = Join-Path $Destination $file.FullName
                $null = New-Item -ItemType Directory -Path (Split-Path $target) -Force
                [IO.Compression.ZipFileExtensions]::ExtractToFile($file, $target, $false)
            }
        } finally { $zip.Dispose() }
    }

    $work = Join-Path ([IO.Path]::GetTempPath()) "appsynergy-bootstrap-$([Guid]::NewGuid().ToString('N'))"
    $verifier = $null
    $null = New-Item -ItemType Directory -Path $work
    try {
        $modulePath = Join-Path $work 'AppSynergy.psm1'
        Receive-BootstrapVerifier $modulePath
        $verifier = Import-Module $modulePath -PassThru -Force
        $key = Join-Path $work 'release.pem'
        $publicKey = @'
-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAmjjqudkHGNcmStCJouJZ
7g4r7F6WjbWUxdIpBHtYfcYIybnVHbGIWtWj9ELbdlovuvpI6P/yaIRRBXKTKEY1
TQ6ULAfBZ74I0MX6ypmKjPVDWLuoHu83ZXaS+tOx9DbRr7vQ05eSWvNmm3kViXPu
ar2BKhdxCMDnsphUJXtwvHFMpb4hBS9lymDUC6J9v9qyN5A1cggmm6cgxGtvkb7k
/FtPcANH0rBC+XDnYRK2gFWHp1sXeJA0FS/UVHi7JDQRLdHFS7x/ADlOs0bxGlp0
fFbr4poB3UBPWxu8LMoRj8pvH1b5VsTNz34WD47doVvmpYDSqh0fDyBKmeyQ76CT
itdPz1EYM0+h8RKqQGtYxhlc8ZLElcq0glBH1e7bravAhmrSLwfEt8RQbm0v08TX
jcFpYObyPy8l9Hb1ZjOusz4UDNsm7gqX8xsgOLjAicfW+6FXtHOevxwTe76w83v3
Ch1iztM5rGg/rhjRILDvDcYRkRsqg+2cqiYPUoSw3qDRAgMBAAE=
-----END PUBLIC KEY-----
'@
        [IO.File]::WriteAllText($key, $publicKey)
        Write-Output 'AppSynergy WSL2 - downloading and verifying the latest accepted release.'
        $latest = 'https://github.com/Appsynergy-io/appsynergy-wsl/releases/latest/download'
        try {
            Receive-BoundedFile "$latest/manifest.json" (Join-Path $work 'manifest.json') 1MB
        } catch {
            throw 'No accepted release could be downloaded. Check the project release status and your network connection. Testing candidates are never installed automatically.'
        }
        Receive-BoundedFile "$latest/manifest.sig" (Join-Path $work 'manifest.sig') 384
        $manifest = Get-VerifiedManifest $work -PublicKey $key -MetadataOnly
        $origin = "https://github.com/Appsynergy-io/appsynergy-wsl/releases/download/appsynergy-wsl-v$($manifest.version)"
        foreach ($name in @('promotion.json', 'promotion.json.rsa.sig', 'acceptance.json')) {
            Receive-BoundedFile "$origin/$name" (Join-Path $work $name) 1MB
        }
        $null = Get-VerifiedPromotion $work $manifest -PublicKey $key
        if ($manifest.artifacts['tools.zip'].size -gt 16MB) { throw 'Installer archive exceeds size limit' }
        $archive = Join-Path $work 'tools.zip'
        Receive-BoundedFile "$origin/tools.zip" $archive $manifest.artifacts['tools.zip'].size
        $destination = Join-Path $work 'tools'
        Expand-VerifiedToolArchive $archive $manifest.artifacts['tools.zip'] $destination
        & (Join-Path $destination 'windows/Install.ps1') -Version $manifest.version
    } finally {
        if ($null -ne $verifier) { Remove-Module $verifier -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}
