-- Qualified-lead attribution (marriage checklist activity).
-- NULL = organic/app lead. 'marriage_checklist' = created by the public
-- checklist activity; such users are onboardees (outside the dating pool)
-- until they deliberately finish onboarding, at which point the tag is
-- carried to person for attribution.
ALTER TABLE onboardee ADD COLUMN IF NOT EXISTS lead_source TEXT;
ALTER TABLE person ADD COLUMN IF NOT EXISTS lead_source TEXT;
