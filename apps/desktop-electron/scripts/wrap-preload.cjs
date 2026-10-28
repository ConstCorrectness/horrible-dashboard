const fs = require('fs');
const path = require('path');

const preloadDist = path.join(__dirname, '../dist/preload.js');
if (fs.existsSync(preloadDist)) {
  let content = fs.readFileSync(preloadDist, 'utf8');
  const polyfill =
    'var exports = typeof exports !== "undefined" ? exports : {};\nvar module = typeof module !== "undefined" ? module : { exports };\n';
  if (!content.includes('var exports = typeof exports')) {
    content = polyfill + content;
    fs.writeFileSync(preloadDist, content, 'utf8');
  }
}
