/** Linkify plain file paths after Markdown parsing, never inside existing links.
 * Working on text nodes preserves destinations, titles, references and code.
 * URL sanitization remains owned by Streamdown's unchanged security pipeline.
 */
const FILE_PATH = /(^|[\s(])(\/files\/(?:artifacts\/[\w.-]+(?:\/[\w./-]+)?\/?|[\w.-]+)(?:\?[^\s<)]*)?)(?=$|[\s<)])/g;
const PROTECTED = new Set(['link', 'linkReference', 'image', 'imageReference', 'definition', 'code', 'inlineCode', 'html']);

function linkText(value) {
  const nodes = [];
  let cursor = 0;
  for (const match of value.matchAll(FILE_PATH)) {
    const start = match.index + match[1].length;
    const href = match[2];
    if (start > cursor) nodes.push({type: 'text', value: value.slice(cursor, start)});
    const leaf = href.split('?', 1)[0].split('/').filter(Boolean).pop() || 'file';
    const label = leaf.includes('_') ? leaf.split('_').slice(1).join('_') || leaf : leaf;
    nodes.push({type: 'link', url: href, children: [{type: 'text', value: label}]});
    cursor = start + href.length;
  }
  if (cursor < value.length) nodes.push({type: 'text', value: value.slice(cursor)});
  return nodes;
}

export function remarkArtifactLinks() {
  return function visit(node) {
    if (PROTECTED.has(node.type) || !Array.isArray(node.children)) return;
    node.children = node.children.flatMap(child => {
      if (child.type === 'text') return linkText(child.value);
      visit(child);
      return [child];
    });
  };
}
