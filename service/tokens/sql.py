Q_BALANCE = """
  SELECT COALESCE(SUM(delta), 0) AS balance
    FROM token_ledger WHERE person_id = %(person_id)s
"""

Q_BALANCE_FOR_UPDATE = """
  SELECT COALESCE(SUM(delta), 0) AS balance
    FROM token_ledger
   WHERE person_id = %(person_id)s
   FOR UPDATE
"""

Q_INSERT_LEDGER = """
  INSERT INTO token_ledger (person_id, delta, reason, metadata)
       VALUES (%(person_id)s, %(delta)s, %(reason)s, %(metadata)s::jsonb)
"""
