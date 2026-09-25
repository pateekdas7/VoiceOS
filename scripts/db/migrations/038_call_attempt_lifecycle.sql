-- Migration 038: canonical telephony call-attempt lifecycle states.
BEGIN;
ALTER TABLE call_attempts DROP CONSTRAINT IF EXISTS call_attempts_status_check;
ALTER TABLE call_attempts ADD CONSTRAINT call_attempts_status_check
  CHECK (status IN (
    'INITIATED','DIALING','RINGING','IN_PROGRESS','COMPLETED',
    'FAILED','TIMEOUT','NO_ANSWER','BUSY','CANCELLED','VOICEMAIL'
  ));
COMMIT;
