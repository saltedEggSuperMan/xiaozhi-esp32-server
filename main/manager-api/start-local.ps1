param([switch]$Check)

$ErrorActionPreference = 'Stop'
$jdkPath = 'D:\devEnvironment\jdk-21.0.12'
$mavenPath = 'D:\devEnvironment\apache-maven-3.10.0'
$java = Join-Path $jdkPath 'bin\java.exe'
$javac = Join-Path $jdkPath 'bin\javac.exe'
$maven = Join-Path $mavenPath 'bin\mvn.cmd'

foreach ($executable in @($java, $javac, $maven)) {
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "Required executable not found: $executable"
    }
}

$previousJavaHome = $env:JAVA_HOME
$previousMavenHome = $env:MAVEN_HOME
$previousPath = $env:PATH
$resultCode = 1
Push-Location $PSScriptRoot
try {
    $env:JAVA_HOME = $jdkPath
    $env:MAVEN_HOME = $mavenPath
    $env:PATH = "$jdkPath\bin;$mavenPath\bin;$previousPath"

    if ($Check) {
        & $java -version
        if ($LASTEXITCODE -ne 0) { throw 'Java runtime check failed.' }
        & $javac -version
        if ($LASTEXITCODE -ne 0) { throw 'Java compiler check failed.' }
        & $maven -version
    } else {
        & $maven '-Dspring-boot.run.profiles=dev' 'spring-boot:run'
    }
    $resultCode = $LASTEXITCODE
} finally {
    $env:JAVA_HOME = $previousJavaHome
    $env:MAVEN_HOME = $previousMavenHome
    $env:PATH = $previousPath
    Pop-Location
}
exit $resultCode
