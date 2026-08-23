const fs = require('fs');

// Read the match detail file
let content = fs.readFileSync('laliga_dashboard/matches_detail/3918318.js', 'utf8');
content = content.replace('window.MATCH_DETAIL = ', '').trim().replace(/;$/, '');
const D = JSON.parse(content);

function testForSide(side) {
  console.log(`\n--- Side: ${side} ---`);
  const maxMin = D.maxMin || 90;
  
  // Get all players (starters + subs)
  const players = D.lineups[side].starters.concat(D.lineups[side].subs);
  
  // Find all transition times
  let transitionMins = [0, maxMin];
  players.forEach(p => {
    if (p.on != null) transitionMins.push(p.on);
    if (p.off != null) transitionMins.push(p.off);
  });
  
  // Sort and unique
  transitionMins = Array.from(new Set(transitionMins)).sort((a, b) => a - b);
  console.log('Transition minutes:', transitionMins);
  
  // Find the longest interval
  let longestStart = 0;
  let longestEnd = maxMin;
  let maxDuration = 0;
  
  for (let i = 0; i < transitionMins.length - 1; i++) {
    const start = transitionMins[i];
    const end = transitionMins[i + 1];
    const duration = end - start;
    if (duration > maxDuration) {
      maxDuration = duration;
      longestStart = start;
      longestEnd = end;
    }
  }
  
  console.log(`Longest interval: ${longestStart} to ${longestEnd} (duration: ${maxDuration} mins)`);
  
  // Find active players at the midpoint of this interval
  const mid = (longestStart + longestEnd) / 2;
  const activePlayers = [];
  players.forEach(p => {
    const on = p.on != null ? p.on : 0;
    const off = p.off != null ? p.off : maxMin;
    if (on <= mid && off >= mid) {
      activePlayers.push({ name: p.name, num: p.num });
    }
  });
  
  console.log('Active players count:', activePlayers.length);
  console.log('Active players:', activePlayers);
}

testForSide('home');
testForSide('away');
