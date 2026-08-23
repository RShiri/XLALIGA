const fs = require('fs');

let content = fs.readFileSync('laliga_dashboard/matches_detail/3918318.js', 'utf8');
content = content.replace('window.MATCH_DETAIL = ', '').trim().replace(/;$/, '');
const D = JSON.parse(content);

function testSlots(side, lo, hi) {
  console.log(`\n=== Side: ${side}, Window: ${lo}-${hi} ===`);
  const maxMin = D.maxMin || 90;
  
  const starters = D.lineups[side].starters.map(p => ({
    name: p.name,
    num: p.num,
    on: p.on != null ? p.on : 0,
    off: p.off != null ? p.off : maxMin
  }));

  const subs = D.lineups[side].subs.map(p => ({
    name: p.name,
    num: p.num,
    on: p.on != null ? p.on : 0,
    off: p.off != null ? p.off : maxMin
  })).sort((a, b) => a.on - b.on);

  // Initialize 11 slots
  const slots = starters.map(p => [p]);

  // Assign subs to slots based on closest off time
  subs.forEach(s => {
    let bestSlot = -1;
    let minDiff = 999;
    slots.forEach((slot, idx) => {
      const lastPlayer = slot[slot.length - 1];
      const diff = Math.abs(lastPlayer.off - s.on);
      if (diff < minDiff) {
        minDiff = diff;
        bestSlot = idx;
      }
    });
    if (bestSlot !== -1) {
      slots[bestSlot].push(s);
    }
  });

  // Print slots
  console.log('Formed Slots:');
  slots.forEach((slot, idx) => {
    console.log(`Slot ${idx + 1}:`, slot.map(p => `${p.name} (${p.on}-${p.off}')`));
  });

  // Filter slot players in window [lo, hi]
  const activePlayers = [];
  slots.forEach(slot => {
    let bestPlayer = null;
    let maxDur = 0;
    slot.forEach(p => {
      const dur = Math.max(0, Math.min(hi, p.off) - Math.max(lo, p.on));
      if (dur > maxDur) {
        maxDur = dur;
        bestPlayer = p;
      }
    });
    if (bestPlayer) {
      activePlayers.push({ name: bestPlayer.name, num: bestPlayer.num, dur: maxDur });
    }
  });

  console.log(`Active players in window (count: ${activePlayers.length}):`);
  console.log(activePlayers);
}

testSlots('home', 81, 96);
