'use strict';

function validTimezone(timeZone) {
  if (!timeZone || typeof timeZone !== 'string') return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone }).format(); return true; } catch { return false; }
}
function hasExplicitOffset(value) { return typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value.trim()); }
function localParts(date, timeZone) {
  const p = new Intl.DateTimeFormat('en-CA', {timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).formatToParts(date).reduce((o,x)=>(o[x.type]=x.value,o),{});
  return {year:p.year,month:p.month,day:p.day,hour:String(parseInt(p.hour,10)%24).padStart(2,'0'),minute:p.minute,second:p.second,millisecond:String(date.getUTCMilliseconds()).padStart(3,'0')};
}
function parseCallbackInstant(callbackAt, timeZone) {
  if (typeof callbackAt !== 'string' || !callbackAt.trim()) throw new Error('callback_at required');
  const raw = callbackAt.trim();
  if (hasExplicitOffset(raw)) { const ms=Date.parse(raw); if(Number.isNaN(ms)) throw new Error('invalid callback_at'); return new Date(ms); }
  if (!validTimezone(timeZone)) throw new Error('callback timezone is required and must be a valid IANA timezone');
  const m=raw.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
  if(!m) throw new Error('callback_at must be ISO-8601 with an explicit offset or a local wall time plus callback_timezone');
  const targetUtcMs=Date.parse(m[1]+'T'+m[2]+':'+m[3]+':'+(m[4]||'00')+'.'+(m[5]||'000').padEnd(3,'0')+'Z');
  let candidate=targetUtcMs;
  for(let i=0;i<5;i++) { const p=localParts(new Date(candidate),timeZone); const observed=Date.parse(p.year+'-'+p.month+'-'+p.day+'T'+p.hour+':'+p.minute+':'+p.second+'.'+p.millisecond+'Z'); candidate += targetUtcMs-observed; }
  const check=localParts(new Date(candidate),timeZone); const expected=m[1]+'T'+m[2]+':'+m[3]+':'+(m[4]||'00'); const actual=check.year+'-'+check.month+'-'+check.day+'T'+check.hour+':'+check.minute+':'+check.second;
  if(actual!==expected) throw new Error('callback local time does not exist in the selected timezone');
  return new Date(candidate);
}
function evaluateWorkingHours(date,timeZone,policy={}) {
  if(!validTimezone(timeZone)) throw new Error('invalid timezone');
  const p=localParts(date,timeZone); const weekday=new Date(p.year+'-'+p.month+'-'+p.day+'T00:00:00Z').getUTCDay()||7;
  const allowed=Array.isArray(policy.allowedWeekdays)&&policy.allowedWeekdays.length?policy.allowedWeekdays.map(Number):[1,2,3,4,5,6,7];
  if(!allowed.includes(weekday)) return {allowed:false,reason:'weekday_disallowed',localDate:p.year+'-'+p.month+'-'+p.day};
  const excluded=Array.isArray(policy.excludedDates)?policy.excludedDates.map(x=>String(x).slice(0,10)):[]; const localDate=p.year+'-'+p.month+'-'+p.day;
  if(excluded.includes(localDate)) return {allowed:false,reason:'excluded_date',localDate};
  const minuteOfDay=Number(p.hour)*60+Number(p.minute);
  const start=policy.dailyStartHour==null?9:Number(policy.dailyStartHour), end=policy.dailyEndHour==null?18:Number(policy.dailyEndHour);
  const startMinute=start*60, endMinute=end*60;
  const inWindow=start<=end?minuteOfDay>=startMinute&&minuteOfDay<endMinute:minuteOfDay>=startMinute||minuteOfDay<endMinute;
  return {allowed:inWindow,reason:inWindow?'within_window':'outside_calling_window',localDate,localHour:Number(p.hour),window:start+'-'+end};
}
module.exports={validTimezone,hasExplicitOffset,parseCallbackInstant,localParts,evaluateWorkingHours};
