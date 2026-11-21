/* Typical values for a few well known places. Rough, from general knowledge; used for an instant start and as a fallback when the live data cannot be reached.
   now/fut: d40 = days a year at or above 40 C, d35 = days at or above 35 C, rainMax = wettest day of the year (mm), annRain = yearly rain (mm), gust = strong gust (km/h). */
window.PRESETS = [
  { name: "Dubai, UAE",        lat: 25.20, lon: 55.27, elev: 5,   coastKm: 1,   quakes: 8,   now: { d40: 25, d35: 120, rainMax: 45,  annRain: 100,  gust: 85 },  fut: { d40: 42, d35: 150, rainMax: 55,  annRain: 95,   gust: 88 } },
  { name: "Riyadh, Saudi Arabia", lat: 24.71, lon: 46.68, elev: 610, coastKm: 400, quakes: 4, now: { d40: 90, d35: 150, rainMax: 30,  annRain: 110,  gust: 80 },  fut: { d40: 115, d35: 175, rainMax: 33, annRain: 100,  gust: 82 } },
  { name: "Jeddah, Saudi Arabia", lat: 21.54, lon: 39.17, elev: 12, coastKm: 2,  quakes: 15, now: { d40: 45, d35: 140, rainMax: 70,  annRain: 60,   gust: 85 },  fut: { d40: 65, d35: 165, rainMax: 85,  annRain: 58,   gust: 88 } },
  { name: "Mumbai, India",     lat: 19.08, lon: 72.88, elev: 10,  coastKm: 3,   quakes: 20,  now: { d40: 0,  d35: 35,  rainMax: 220, annRain: 2400, gust: 95 },  fut: { d40: 2,  d35: 60,  rainMax: 260, annRain: 2600, gust: 100 } },
  { name: "Lagos, Nigeria",    lat: 6.52,  lon: 3.38,  elev: 6,   coastKm: 3,   quakes: 1,   now: { d40: 0,  d35: 15,  rainMax: 140, annRain: 1800, gust: 85 },  fut: { d40: 0,  d35: 40,  rainMax: 165, annRain: 1850, gust: 88 } },
  { name: "Miami, USA",        lat: 25.76, lon: -80.19, elev: 2,  coastKm: 1,   quakes: 0,   now: { d40: 0,  d35: 8,   rainMax: 160, annRain: 1550, gust: 170 }, fut: { d40: 0,  d35: 35,  rainMax: 190, annRain: 1600, gust: 180 } },
  { name: "Rotterdam, Netherlands", lat: 51.92, lon: 4.48, elev: -2, coastKm: 12, quakes: 2, now: { d40: 0,  d35: 1,   rainMax: 40,  annRain: 800,  gust: 100 }, fut: { d40: 0,  d35: 4,   rainMax: 50,  annRain: 830,  gust: 102 } },
  { name: "Tokyo, Japan",      lat: 35.68, lon: 139.69, elev: 40, coastKm: 8,   quakes: 300, now: { d40: 0,  d35: 14,  rainMax: 120, annRain: 1500, gust: 110 }, fut: { d40: 3,  d35: 30,  rainMax: 140, annRain: 1550, gust: 112 } },
  { name: "Addis Ababa, Ethiopia", lat: 9.03, lon: 38.74, elev: 2355, coastKm: 700, quakes: 25, now: { d40: 0, d35: 0, rainMax: 60, annRain: 1200, gust: 60 }, fut: { d40: 0, d35: 0, rainMax: 70, annRain: 1250, gust: 61 } }
];
