import { readFileSync } from 'node:fs';
import { calculateZiweiRaw } from '../ziwei/iztro-adapter.js';

const payload = JSON.parse(readFileSync(0, 'utf8'));
const [year, month, day] = payload.date.split('-').map(Number);
const [hour, minute] = payload.time.split(':').map(Number);

const raw = calculateZiweiRaw({
  solarDate: `${year}-${month}-${day}`,
  hour,
  minute,
  gender: payload.gender,
  birthContext: {
    location: payload.place,
    timezone: payload.timezone ?? null,
    latitude: payload.latitude ?? null,
    longitude: payload.longitude ?? null,
    timeNormalizationMethod: 'civil-time; birthplace supplied for audit',
  },
});

process.stdout.write(JSON.stringify(raw));
