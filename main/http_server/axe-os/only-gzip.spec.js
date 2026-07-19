const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { removeUncompressedFiles } = require('./only-gzip');

test('removeUncompressedFiles recursively keeps only gzip assets', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'axe-os-gzip-'));
  const nested = path.join(directory, 'assets', 'i18n');
  fs.mkdirSync(nested, { recursive: true });

  fs.writeFileSync(path.join(directory, 'index.html'), 'html');
  fs.writeFileSync(path.join(directory, 'index.html.gz'), 'gzip');
  fs.writeFileSync(path.join(nested, 'en.json'), '{}');
  fs.writeFileSync(path.join(nested, 'en.json.gz'), 'gzip');

  try {
    const removed = removeUncompressedFiles(directory);
    assert.equal(removed.length, 2);
    assert.deepEqual(fs.readdirSync(directory).sort(), ['assets', 'index.html.gz']);
    assert.deepEqual(fs.readdirSync(nested), ['en.json.gz']);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('removeUncompressedFiles fails clearly for a missing build directory', () => {
  assert.throws(
    () => removeUncompressedFiles(path.join(os.tmpdir(), 'axe-os-missing-directory')),
    /Build output directory does not exist/
  );
});
