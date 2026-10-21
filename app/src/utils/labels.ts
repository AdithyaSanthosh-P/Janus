// What kind of action a card is, in words ("SmartThings · Action"), instead of
// the raw tool name ("ST_CLOSE_GARAGE").
export function cardCategory(tool: string, kind?: string): string {
  const area = tool.startsWith('st_') ? 'SmartThings' : 'Device care';
  return `${area} · ${kind === 'read' ? 'Check' : 'Action'}`;
}
