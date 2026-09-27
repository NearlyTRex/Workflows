; Exercises inno-setup.yml: the smallest installer that proves a compile, a
; version passed in with /DAppVersion, and a silent install.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppName=Workflows Fixture
AppVersion={#AppVersion}
DefaultDirName={autopf}\WorkflowsFixture
OutputDir=dist
OutputBaseFilename=Fixture-{#AppVersion}-Setup
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "readme.txt"; DestDir: "{app}"
