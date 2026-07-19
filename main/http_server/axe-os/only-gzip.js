const fs = require('fs');
const path = require('path');

const defaultDirectory = path.join(__dirname, 'dist', 'axe-os');

/**
 * Remove every uncompressed file from an Angular build after gzipper has
 * produced its `.gz` counterpart. The firmware HTTP server always appends
 * `.gz` to requested paths, so retaining originals only wastes SPIFFS space.
 */
function removeUncompressedFiles(directory = defaultDirectory) {
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    throw new Error(`Build output directory does not exist: ${directory}`);
  }

  const removed = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      removed.push(...removeUncompressedFiles(entryPath));
    } else if (entry.isFile() && !entry.name.endsWith('.gz')) {
      fs.unlinkSync(entryPath);
      removed.push(entryPath);
    }
  }

  return removed;
}

if (require.main === module) {
  const directory = process.argv[2]
    ? path.resolve(process.argv[2])
    : defaultDirectory;
  const removed = removeUncompressedFiles(directory);
  console.log(`Removed ${removed.length} uncompressed files from ${directory}`);
}

module.exports = { removeUncompressedFiles };
