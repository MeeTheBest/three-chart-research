import { astro } from 'iztro';

export const IZTRO_ENGINE = Object.freeze({
  name: 'iztro',
  version: '2.5.8',
});

// Pin every rule that iztro otherwise supplies as a process-wide default.
// If another school is added later, run it as a separately named profile.
export const ZIWEI_RULE_PROFILE = Object.freeze({
  id: 'iztro-default-v1',
  yearDivide: 'normal',
  horoscopeDivide: 'normal',
  ageDivide: 'normal',
  dayDivide: 'forward',
  algorithm: 'default',
  fixLeap: true,
});

function requireInteger(value, label, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new RangeError(`${label} must be an integer from ${min} to ${max}`);
  }
}

function normalizeSolarDate(value) {
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value ?? '');
  if (!match) {
    throw new TypeError('solarDate must use YYYY-M-D or YYYY-MM-DD');
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));

  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    throw new RangeError('solarDate is not a valid Gregorian date');
  }

  return `${year}-${month}-${day}`;
}

/** Convert normalized local civil clock time to iztro's 0-12 time index. */
export function clockTimeToIztroIndex(hour, minute) {
  requireInteger(hour, 'hour', 0, 23);
  requireInteger(minute, 'minute', 0, 59);

  if (hour === 0) return 0;
  if (hour === 23) return 12;
  return Math.floor((hour + 1) / 2);
}

function boundarySensitivity(hour, minute, thresholdMinutes = 15) {
  const value = hour * 60 + minute;
  const boundaries = [0, 60, 180, 300, 420, 540, 660, 780, 900, 1020, 1140, 1260, 1380, 1440];
  const distance = Math.min(...boundaries.map((boundary) => Math.abs(value - boundary)));

  return {
    thresholdMinutes,
    minutesToNearestShichenBoundary: distance,
    sensitive: distance <= thresholdMinutes,
  };
}

function serializeStar(star) {
  return {
    name: star.name,
    type: star.type,
    scope: star.scope,
    brightness: star.brightness ?? null,
    mutagen: star.mutagen ?? null,
  };
}

function serializePalace(palace) {
  return {
    index: palace.index,
    name: palace.name,
    heavenlyStem: palace.heavenlyStem,
    earthlyBranch: palace.earthlyBranch,
    isBodyPalace: palace.isBodyPalace,
    isOriginalPalace: palace.isOriginalPalace,
    majorStars: palace.majorStars.map(serializeStar),
    minorStars: palace.minorStars.map(serializeStar),
    adjectiveStars: palace.adjectiveStars.map(serializeStar),
    changsheng12: palace.changsheng12,
    boshi12: palace.boshi12,
    jiangqian12: palace.jiangqian12,
    suiqian12: palace.suiqian12,
    decadal: {
      range: [...palace.decadal.range],
      heavenlyStem: palace.decadal.heavenlyStem,
      earthlyBranch: palace.decadal.earthlyBranch,
    },
    ages: [...palace.ages],
  };
}

function serializeHoroscopeItem(item) {
  return {
    index: item.index,
    name: item.name,
    heavenlyStem: item.heavenlyStem,
    earthlyBranch: item.earthlyBranch,
    palaceNames: [...item.palaceNames],
    mutagen: [...item.mutagen],
    stars: (item.stars ?? []).map((palaceStars) => palaceStars.map(serializeStar)),
  };
}

function serializeHoroscopeRanges(chart, dates) {
  const ranges = [];
  for (const targetDate of dates) {
    const horoscope = chart.horoscope(targetDate, 6);
    const payload = {
      lunarDate: horoscope.lunarDate,
      decadal: serializeHoroscopeItem(horoscope.decadal),
      age: {
        ...serializeHoroscopeItem(horoscope.age),
        nominalAge: horoscope.age.nominalAge,
      },
      yearly: {
        ...serializeHoroscopeItem(horoscope.yearly),
        yearlyDecStar: {
          jiangqian12: [...horoscope.yearly.yearlyDecStar.jiangqian12],
          suiqian12: [...horoscope.yearly.yearlyDecStar.suiqian12],
        },
      },
      monthly: serializeHoroscopeItem(horoscope.monthly),
    };
    const signature = JSON.stringify({
      decadal: [payload.decadal.index, payload.decadal.heavenlyStem, payload.decadal.earthlyBranch],
      age: [payload.age.index, payload.age.heavenlyStem, payload.age.earthlyBranch],
      yearly: [payload.yearly.index, payload.yearly.heavenlyStem, payload.yearly.earthlyBranch],
      monthly: [payload.monthly.index, payload.monthly.heavenlyStem, payload.monthly.earthlyBranch],
    });
    const previous = ranges.at(-1);
    if (previous?.signature === signature) {
      previous.endDate = targetDate;
      previous.lunarEndDate = payload.lunarDate;
    } else {
      ranges.push({
        startDate: targetDate,
        endDate: targetDate,
        lunarStartDate: payload.lunarDate,
        lunarEndDate: payload.lunarDate,
        signature,
        ...payload,
      });
    }
  }
  return ranges.map(({ signature, ...value }) => value);
}

/**
 * Produce JSON-safe Raw Data for the upper research/interpretation layer.
 *
 * solarDate/hour/minute must already be normalized to the selected local-time
 * policy. iztro does not consume an IANA timezone, coordinates, or minutes.
 */
export function calculateZiweiRaw({
  solarDate,
  hour,
  minute,
  gender,
  birthContext = {},
  horoscopeDates = [],
}) {
  const normalizedSolarDate = normalizeSolarDate(solarDate);
  requireInteger(hour, 'hour', 0, 23);
  requireInteger(minute, 'minute', 0, 59);

  if (gender !== '男' && gender !== '女') {
    throw new TypeError("gender must be '男' or '女'");
  }

  const timeIndex = clockTimeToIztroIndex(hour, minute);
  const sensitivity = boundarySensitivity(hour, minute);

  astro.config({
    yearDivide: ZIWEI_RULE_PROFILE.yearDivide,
    horoscopeDivide: ZIWEI_RULE_PROFILE.horoscopeDivide,
    ageDivide: ZIWEI_RULE_PROFILE.ageDivide,
    dayDivide: ZIWEI_RULE_PROFILE.dayDivide,
    algorithm: ZIWEI_RULE_PROFILE.algorithm,
  });

  const chart = astro.bySolar(
    normalizedSolarDate,
    timeIndex,
    gender,
    ZIWEI_RULE_PROFILE.fixLeap,
    'zh-CN',
  );

  const warnings = [
    'iztro uses the two-hour shichen index; the input minute is retained for audit but is not otherwise used by the chart engine.',
    'iztro does not consume birthplace, coordinates, or timezone; these must be normalized before this adapter is called.',
  ];

  if (sensitivity.sensitive) {
    warnings.push('Birth time is close to a shichen boundary; run adjacent-shichen sensitivity analysis.');
  }

  return {
    schemaVersion: 'ziwei.raw.v1',
    engine: IZTRO_ENGINE,
    ruleProfile: ZIWEI_RULE_PROFILE,
    inputAudit: {
      normalizedLocalCivilTime: {
        solarDate: normalizedSolarDate,
        hour,
        minute,
        timeIndex,
        timeName: chart.time,
        timeRange: chart.timeRange,
      },
      birthContext: {
        location: birthContext.location ?? null,
        timezone: birthContext.timezone ?? null,
        latitude: birthContext.latitude ?? null,
        longitude: birthContext.longitude ?? null,
        timeNormalizationMethod: birthContext.timeNormalizationMethod ?? null,
      },
      sensitivity,
      warnings,
      horoscopeDates: [...horoscopeDates],
    },
    chart: {
      gender: chart.gender,
      solarDate: chart.solarDate,
      lunarDate: chart.lunarDate,
      chineseDate: chart.chineseDate,
      zodiac: chart.zodiac,
      sign: chart.sign,
      soulPalaceEarthlyBranch: chart.earthlyBranchOfSoulPalace,
      bodyPalaceEarthlyBranch: chart.earthlyBranchOfBodyPalace,
      soulMaster: chart.soul,
      bodyMaster: chart.body,
      fiveElementsClass: chart.fiveElementsClass,
      palaces: chart.palaces.map(serializePalace),
      horoscopeRanges: serializeHoroscopeRanges(chart, horoscopeDates),
    },
  };
}
