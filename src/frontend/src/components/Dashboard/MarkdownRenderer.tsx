import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Tiny inline parser: bold, italic, inline-code, links
// ---------------------------------------------------------------------------
function parseInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Pattern: **bold**, *italic*, `code`
  const pattern = /(\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    if (match[2] !== undefined) {
      nodes.push(<strong key={match.index}>{match[2]}</strong>);
    } else if (match[3] !== undefined) {
      nodes.push(<em key={match.index}>{match[3]}</em>);
    } else if (match[4] !== undefined) {
      nodes.push(<code key={match.index} className="md-inline-code">{match[4]}</code>);
    }
    last = match.index + match[0].length;
  }

  if (last < text.length) {
    nodes.push(text.slice(last));
  }

  return nodes.length ? nodes : [text];
}

// ---------------------------------------------------------------------------
// Table parser
// ---------------------------------------------------------------------------
function parseTable(lines: string[]): ReactNode | null {
  // Need at least: header row + separator row + 1 data row
  if (lines.length < 2) return null;
  const sepIndex = lines.findIndex((l) => /^\|?[\s\-|:]+\|?$/.test(l));
  if (sepIndex !== 1) return null;

  const parseRow = (line: string) =>
    line
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const headers = parseRow(lines[0]);
  const rows = lines.slice(2).map(parseRow);

  return (
    <div className="md-table-wrap" key={lines[0]}>
      <table className="md-table">
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i}>{parseInline(h)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci}>{parseInline(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Block-level tokeniser
// ---------------------------------------------------------------------------
type Block =
  | { type: "heading"; level: 1 | 2 | 3 | 4; text: string }
  | { type: "hr" }
  | { type: "code"; lang: string; code: string }
  | { type: "table"; lines: string[] }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[]; start: number }
  | { type: "blockquote"; text: string }
  | { type: "paragraph"; text: string }
  | { type: "blank" };

function tokenise(markdown: string): Block[] {
  const lines = markdown.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Blank line
    if (/^\s*$/.test(line)) {
      blocks.push({ type: "blank" });
      i++;
      continue;
    }

    // Fenced code block
    if (/^\s*```/.test(line)) {
      const lang = line.replace(/^\s*```/, "").trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // closing ```
      blocks.push({ type: "code", lang, code: codeLines.join("\n") });
      continue;
    }

    // Heading
    const headingMatch = /^\s*(#{1,4})\s+(.+)$/.exec(line);
    if (headingMatch) {
      blocks.push({
        type: "heading",
        level: headingMatch[1].length as 1 | 2 | 3 | 4,
        text: headingMatch[2],
      });
      i++;
      continue;
    }

    // Horizontal rule
    if (/^\s*---+$/.test(line.trim()) || /^\s*\*\*\*+$/.test(line.trim())) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const text = line.replace(/^\s*>\s?/, "");
      blocks.push({ type: "blockquote", text });
      i++;
      continue;
    }

    // Table (line contains |)
    if (/\|/.test(line)) {
      const tableLines: string[] = [];
      while (i < lines.length && /\|/.test(lines[i])) {
        tableLines.push(lines[i]);
        i++;
      }
      blocks.push({ type: "table", lines: tableLines });
      continue;
    }

    // Unordered list
    if (/^\s*[-*+]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s/.test(line)) {
      const match = /^\s*(\d+)\.\s/.exec(line);
      const start = match ? parseInt(match[1], 10) : 1;
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", items, start });
      continue;
    }

    // Paragraph — collect until blank or block-level element
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^\s*#{1,4}\s/.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*[-*+]\s/.test(lines[i]) &&
      !/^\s*\d+\.\s/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*---+$/.test(lines[i].trim()) &&
      !/\|/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length) {
      blocks.push({ type: "paragraph", text: paraLines.join("\n") });
    }
  }

  return blocks;
}

// ---------------------------------------------------------------------------
// Render blocks → JSX
// ---------------------------------------------------------------------------
function renderBlock(block: Block, index: number): ReactNode {
  switch (block.type) {
    case "blank":
      return null;

    case "heading": {
      const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
      return (
        <Tag key={index} className={`md-h${block.level}`}>
          {parseInline(block.text)}
        </Tag>
      );
    }

    case "hr":
      return <hr key={index} className="md-hr" />;

    case "code":
      return (
        <pre key={index} className="md-code-block">
          {block.lang && <div className="md-code-lang">{block.lang}</div>}
          <code>{block.code}</code>
        </pre>
      );

    case "table":
      return <div key={index}>{parseTable(block.lines)}</div>;

    case "ul":
      return (
        <ul key={index} className="md-ul">
          {block.items.map((item, ii) => (
            <li key={ii}>{parseInline(item)}</li>
          ))}
        </ul>
      );

    case "ol":
      return (
        <ol key={index} className="md-ol" start={block.start}>
          {block.items.map((item, ii) => (
            <li key={ii}>{parseInline(item)}</li>
          ))}
        </ol>
      );

    case "blockquote":
      return (
        <blockquote key={index} className="md-blockquote">
          {parseInline(block.text)}
        </blockquote>
      );

    case "paragraph":
      return (
        <p key={index} className="md-p">
          {parseInline(block.text)}
        </p>
      );

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------
export function MarkdownRenderer({ content }: { content: string }) {
  if (!content) return null;
  const blocks = tokenise(content);
  return (
    <div className="md-body">
      {blocks.map((block, i) => renderBlock(block, i))}
    </div>
  );
}
