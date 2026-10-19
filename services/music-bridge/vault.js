'use strict';
const {execFileSync} = require('node:child_process');
// The account cookie travels only over child stdin/stdout, never command arguments.
function protect(text, decrypt=false) {
  const method = decrypt ? 'Unprotect' : 'Protect';
  const script = 'Add-Type -AssemblyName System.Security; ' +
    '[Convert]::ToBase64String([Security.Cryptography.ProtectedData]::' + method +
    '([Convert]::FromBase64String([Console]::In.ReadToEnd()),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))';
  const input = decrypt ? text : Buffer.from(text,'utf8').toString('base64');
  const output = execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],
    {input, encoding:'utf8', windowsHide:true, timeout:10000, stdio:['pipe','pipe','pipe']}).trim();
  return decrypt ? Buffer.from(output,'base64').toString('utf8') : output;
}
module.exports = {protect};
