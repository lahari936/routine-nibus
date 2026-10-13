// Everything is shown in the home's time zone, not the viewer's.

const partsCache = new Map();
function formatter(tz) {
  if (!partsCache.has(tz)) {
    partsCache.set(tz, new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    }));
  }
  return partsCache.get(tz);
}

export function localParts(date, tz) {
  const p = Object.fromEntries(formatter(tz).formatToParts(date).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, minutes: +p.hour * 60 + +p.minute + +p.second / 60, y: +p.year, m: +p.month, d: +p.day, h: +p.hour };
}

/** UTC instant of local midnight starting `isoDate` in `tz`. */
export function localMidnight(isoDate, tz) {
  const [y, m, d] = isoDate.split("-").map(Number);
  let guess = Date.UTC(y, m - 1, d);
  for (let i = 0; i < 2; i++) {
    const p = localParts(new Date(guess), tz);
    const shown = Date.UTC(p.y, p.m - 1, p.d) + p.minutes * 60000;
    guess += Date.UTC(y, m - 1, d) - shown;
  }
  return new Date(guess);
}

export function addDays(isoDate, n) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

export const today = (tz) => localParts(new Date(), tz).date;
export const weekdayIndex = (isoDate) => (new Date(isoDate + "T12:00:00Z").getUTCDay() + 6) % 7; // Monday = 0

export function clock(minutes) {
  const h = Math.floor(minutes / 60) % 24, m = Math.round(minutes % 60);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

export function prettyDate(isoDate) {
  return new Date(isoDate + "T12:00:00Z").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}
