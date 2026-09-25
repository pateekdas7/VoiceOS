'use strict';
const {validTimezone,parseCallbackInstant,evaluateWorkingHours}=require('../../../telephony_callback_policy');
describe('W2 callback timezone policy',()=>{
 test('rejects missing and invalid timezone for naive local time',()=>{expect(()=>parseCallbackInstant('2026-09-25T10:00:00')).toThrow(/timezone/);expect(()=>parseCallbackInstant('2026-09-25T10:00:00','Not/AZone')).toThrow(/timezone/);});
 test('preserves explicit UTC offset',()=>{expect(parseCallbackInstant('2026-09-25T10:00:00+05:30').toISOString()).toBe('2026-09-25T04:30:00.000Z');});
 test('campaign timezone converts local callback to UTC',()=>{expect(parseCallbackInstant('2026-09-25T10:00:00','Asia/Kolkata').toISOString()).toBe('2026-09-25T04:30:00.000Z');});
 test('DST spring-forward nonexistent wall time is rejected',()=>{expect(()=>parseCallbackInstant('2026-03-08T02:30:00','America/New_York')).toThrow(/does not exist/);});
 test('working-hours boundary is deterministic',()=>{expect(evaluateWorkingHours(new Date('2026-09-25T04:30:00.000Z'),'Asia/Kolkata',{dailyStartHour:9,dailyEndHour:18}).allowed).toBe(true);expect(evaluateWorkingHours(new Date('2026-09-25T13:00:00.000Z'),'Asia/Kolkata',{dailyStartHour:9,dailyEndHour:18}).allowed).toBe(false);});
 test('validTimezone accepts IANA zones',()=>{expect(validTimezone('Europe/Berlin')).toBe(true);});
});