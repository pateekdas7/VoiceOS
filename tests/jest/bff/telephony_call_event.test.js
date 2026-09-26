'use strict';
const {buildCanonicalCallEvent}=require('../../../telephony_call_event');
describe('W2 canonical telephony event',()=>{
 test('has durable deterministic identity and downstream fields',()=>{
  const a=buildCanonicalCallEvent({tenantId:'t1',callSid:'CA1',callAttemptId:'a1',lifecycleState:'CONNECTED',durationSeconds:4,correlationId:'tr1'});
  const b=buildCanonicalCallEvent({tenantId:'t1',callSid:'CA1',callAttemptId:'a1',lifecycleState:'CONNECTED',durationSeconds:4,correlationId:'tr1'});
  expect(a.eventId).toBe(b.eventId); expect(a.schemaVersion).toBe('1.0'); expect(a.sequenceNo).toBe(30); expect(a.providerCallSid).toBe('CA1');
 });
 test('tenant or lifecycle changes produce distinct identities',()=>{
  const a=buildCanonicalCallEvent({tenantId:'t1',callSid:'CA1',lifecycleState:'RINGING'});
  const b=buildCanonicalCallEvent({tenantId:'t2',callSid:'CA1',lifecycleState:'RINGING'});
  const c=buildCanonicalCallEvent({tenantId:'t1',callSid:'CA1',lifecycleState:'CONNECTED'});
  expect(a.eventId).not.toBe(b.eventId); expect(a.eventId).not.toBe(c.eventId);
 });
});
