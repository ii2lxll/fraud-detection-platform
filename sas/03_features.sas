/*=====================================================================
  03_features.sas
  Feature engineering: turn each transaction into the signals a fraud model learns from.

  THE GOLDEN RULE: every feature uses only information available BEFORE the
  transaction happened. A model trained on information from the future
  looks brilliant in testing and fails in production ("leakage").

  Output: FRAUD.TXN_FEATURES, one row per transaction.

  Validation (a "parallel run"): the same definitions are built independently
  in python/feature_reference.py. This program exports its totals to
  reports/feature_totals_sas.csv; the Python script compares them and must
  report MATCH for every feature.

  SAS techniques used:
    BY-group processing   FIRST.variable resets the history for each account / customer
    temporary arrays      circular buffers holding recent events (fast look-back windows)
    hash objects          remember which payees, merchants, devices an account has used
    interleaving          SET with BY merges logins and transactions in time order

  Run 00_setup.sas and 01_import_mart.sas first.
=====================================================================*/

%let txn_buffer   = 500;   /* earlier transactions kept per account (max needed in this data: about 55)  */
%let login_buffer = 100;   /* earlier logins kept per customer (max needed in this data: about 6)       */


/*---------------------------------------------------------------- 1. account history features
  One pass over each account's transactions in time order. For each transaction:
    minutes_since_prev_txn  minutes since the account's previous transaction
    txns_prev_1h / _24h     earlier transactions in the last hour / day
    declined_prev_24h       earlier DECLINED transactions in the last day
    debit_amount_prev_24h   money already sent out successfully in the last day
    prior_debit_count       successful debits before this one (all history)
    prior_avg_debit         their average amount
    amount_to_avg           this amount / prior_avg_debit         (debits only)
    drain_ratio             this amount / balance before          (debits only)
    new_payee               first transfer to this payee?         (transfers only)
    new_merchant            first purchase at this merchant?      (card purchases only)
    new_device_for_account  first use of this device on this account? (mobile/web only)
*/
proc sort data=fraud.fact_transactions out=work.tx_sorted;
    by account_key txn_timestamp txn_id;
run;

data work.account_features
        (keep=txn_id minutes_since_prev_txn txns_prev_1h txns_prev_24h declined_prev_24h
              debit_amount_prev_24h prior_debit_count prior_avg_debit amount_to_avg drain_ratio
              new_payee new_merchant new_device_for_account);
    set work.tx_sorted end=last;
    by account_key;

    /* circular buffers: slot = (event number) mod buffer size */
    array buf_time [0:%eval(&txn_buffer - 1)] _temporary_;
    array buf_amt  [0:%eval(&txn_buffer - 1)] _temporary_;
    array buf_decl [0:%eval(&txn_buffer - 1)] _temporary_;

    /* _n_seen, _prev_time, _n_debits and _sum_debits carry over from one row to the next */
    retain _n_seen _prev_time _n_debits _sum_debits _overflows 0;

    if _n_ = 1 then do;
        declare hash payees();
        payees.defineKey('account_key', 'counterparty_account');
        payees.defineDone();
        declare hash merchants();
        merchants.defineKey('account_key', 'merchant_key');
        merchants.defineDone();
        declare hash devices();
        devices.defineKey('account_key', 'device_id');
        devices.defineDone();
    end;

    if first.account_key then do;
        _n_seen = 0; _prev_time = .; _n_debits = 0; _sum_debits = 0;
    end;

    /* --- look back over earlier transactions, newest first, stop once older than 24 hours --- */
    txns_prev_1h = 0; txns_prev_24h = 0; declined_prev_24h = 0; debit_amount_prev_24h = 0;
    do _k = _n_seen - 1 to max(0, _n_seen - &txn_buffer) by -1;
        _slot = mod(_k, &txn_buffer);
        _age  = txn_timestamp - buf_time[_slot];
        if _age > 86400 then leave;
        txns_prev_24h         = txns_prev_24h + 1;
        declined_prev_24h     = declined_prev_24h + buf_decl[_slot];
        debit_amount_prev_24h = debit_amount_prev_24h + buf_amt[_slot];
        if _age <= 3600 then txns_prev_1h = txns_prev_1h + 1;
    end;
    /* safety: if the OLDEST buffered event is still inside the window, the buffer was too small */
    if _n_seen > &txn_buffer then do;
        if txn_timestamp - buf_time[mod(_n_seen - &txn_buffer, &txn_buffer)] <= 86400 then _overflows + 1;
    end;

    /* --- values that describe history BEFORE this transaction --- */
    if not missing(_prev_time) then minutes_since_prev_txn = (txn_timestamp - _prev_time) / 60;
    prior_debit_count = _n_debits;
    if _n_debits > 0 then prior_avg_debit = _sum_debits / _n_debits;
    if direction = 'DR' and _n_debits > 0 then amount_to_avg = amount / prior_avg_debit;
    if direction = 'DR' and balance_before > 0 then drain_ratio = amount / balance_before;

    /* --- first time seen? check the hash, then remember this one --- */
    new_payee = .; new_merchant = .; new_device_for_account = .;
    if txn_type = 'TRANSFER_OUT' and not missing(counterparty_account) then do;
        new_payee = (payees.check() ne 0);
        if new_payee then payees.add();
    end;
    if txn_type = 'CARD_PURCHASE' then do;
        new_merchant = (merchants.check() ne 0);
        if new_merchant then merchants.add();
    end;
    if channel in ('MOBILE', 'WEB') and not missing(device_id) then do;
        new_device_for_account = (devices.check() ne 0);
        if new_device_for_account then devices.add();
    end;

    /* --- add THIS transaction to the history, for the transactions that follow --- */
    _slot = mod(_n_seen, &txn_buffer);
    buf_time[_slot] = txn_timestamp;
    buf_amt[_slot]  = ifn(direction = 'DR' and status = 'SUCCESS', amount, 0);
    buf_decl[_slot] = (status = 'DECLINED');
    _n_seen = _n_seen + 1;
    _prev_time = txn_timestamp;
    if direction = 'DR' and status = 'SUCCESS' then do;
        _n_debits  = _n_debits + 1;
        _sum_debits = _sum_debits + amount;
    end;

    if last then do;
        if _overflows > 0 then put "ERROR: Transaction buffer too small " _overflows= "- increase %nrstr(&)txn_buffer (now &txn_buffer).";
        else put "NOTE: Account history features built. Transaction buffer was large enough.";
    end;
run;


/*---------------------------------------------------------------- 2. login features
  Logins and transactions of the same customer are merged in time order
  (INTERLEAVING: SET two datasets with BY). When a login and a transaction have
  the same second, the login comes first because its dataset is listed first.
  For each transaction, count the customer's logins in the 60 minutes before it:
    logins_prev_1h, failed_logins_prev_1h, foreign_logins_prev_1h, unknown_device_logins_prev_1h
*/
data work.login_events;
    set fraud.fact_logins (keep=customer_key event_timestamp event_id is_failed is_foreign_ip device_known);
    rename event_timestamp = event_time;
run;
proc sort data=work.login_events;
    by customer_key event_time event_id;
run;

data work.txn_events;
    set fraud.fact_transactions (keep=customer_key txn_timestamp txn_id);
    rename txn_timestamp = event_time;
run;
proc sort data=work.txn_events;
    by customer_key event_time txn_id;
run;

data work.login_features
        (keep=txn_id logins_prev_1h failed_logins_prev_1h foreign_logins_prev_1h unknown_device_logins_prev_1h);
    set work.login_events (in=is_login) work.txn_events (in=is_txn) end=last;
    by customer_key event_time;

    array lg_time    [0:%eval(&login_buffer - 1)] _temporary_;
    array lg_failed  [0:%eval(&login_buffer - 1)] _temporary_;
    array lg_foreign [0:%eval(&login_buffer - 1)] _temporary_;
    array lg_unknown [0:%eval(&login_buffer - 1)] _temporary_;
    retain _n_logins _overflows 0;

    if first.customer_key then _n_logins = 0;

    if is_login then do;                         /* remember the login */
        _slot = mod(_n_logins, &login_buffer);
        lg_time[_slot]    = event_time;
        lg_failed[_slot]  = (is_failed = 1);
        lg_foreign[_slot] = (is_foreign_ip = 1);
        lg_unknown[_slot] = (device_known = 0);  /* a missing device_known counts as 0 here: not unknown */
        _n_logins = _n_logins + 1;
    end;
    else do;                                     /* a transaction: count recent logins, then output */
        logins_prev_1h = 0; failed_logins_prev_1h = 0; foreign_logins_prev_1h = 0; unknown_device_logins_prev_1h = 0;
        do _k = _n_logins - 1 to max(0, _n_logins - &login_buffer) by -1;
            _slot = mod(_k, &login_buffer);
            if event_time - lg_time[_slot] > 3600 then leave;
            logins_prev_1h                = logins_prev_1h + 1;
            failed_logins_prev_1h         = failed_logins_prev_1h + lg_failed[_slot];
            foreign_logins_prev_1h        = foreign_logins_prev_1h + lg_foreign[_slot];
            unknown_device_logins_prev_1h = unknown_device_logins_prev_1h + lg_unknown[_slot];
        end;
        if _n_logins > &login_buffer then do;
            if event_time - lg_time[mod(_n_logins - &login_buffer, &login_buffer)] <= 3600 then _overflows + 1;
        end;
        output;
    end;

    if last then do;
        if _overflows > 0 then put "ERROR: Login buffer too small " _overflows= "- increase %nrstr(&)login_buffer (now &login_buffer).";
        else put "NOTE: Login features built. Login buffer was large enough.";
    end;
run;


/*---------------------------------------------------------------- 3. the feature table
  Join the features with context from the dimensions, and the labels.
    in_scope          1 for the transaction types where fraud happens (EDA: transfers out, card purchases)
    days_to_data_end  days between the transaction and the end of the data: used later for the
                      label maturity window (recent fraud is not reported yet)
*/
proc sql;
    create table fraud.txn_features as
    select t.txn_id, t.txn_timestamp, t.date_key, t.account_key, t.customer_key, t.merchant_key,
           t.txn_type, t.channel, t.amount, t.status,
           /* account history */
           a.minutes_since_prev_txn, a.txns_prev_1h, a.txns_prev_24h, a.declined_prev_24h,
           a.debit_amount_prev_24h, a.prior_debit_count, a.prior_avg_debit, a.amount_to_avg, a.drain_ratio,
           a.new_payee, a.new_merchant, a.new_device_for_account,
           /* logins in the hour before */
           l.logins_prev_1h, l.failed_logins_prev_1h, l.foreign_logins_prev_1h, l.unknown_device_logins_prev_1h,
           /* calendar */
           (t.hour_of_day between 0 and 5) as is_night,
           d.is_weekend,
           /* context: who, which account, which merchant */
           t.device_known, c.age, c.segment, c.kyc_risk_rating, c.tenure_days, acc.account_age_days,
           m.category as merchant_category, m.risk_tier as merchant_risk_tier, m.is_foreign as merchant_is_foreign,
           /* scope, maturity and labels */
           (t.txn_type in ('TRANSFER_OUT', 'CARD_PURCHASE')) as in_scope,
           (select max(calendar_date) from fraud.dim_date) - datepart(t.txn_timestamp) as days_to_data_end,
           t.fraud_label, t.claim_status, t.confirmed_date
    from fraud.fact_transactions as t
    left join work.account_features as a   on a.txn_id = t.txn_id
    left join work.login_features   as l   on l.txn_id = t.txn_id
    left join fraud.dim_date        as d   on d.date_key = t.date_key
    left join fraud.dim_customer    as c   on c.customer_key = t.customer_key
    left join fraud.dim_account     as acc on acc.account_key = t.account_key
    left join fraud.dim_merchant    as m   on m.merchant_key = t.merchant_key
    order by t.txn_id;
quit;


/*---------------------------------------------------------------- 4. self-checks */
proc sql noprint;
    select count(*), count(distinct txn_id) into :n_features trimmed, :n_distinct trimmed from fraud.txn_features;
    select count(*) into :n_transactions trimmed from fraud.fact_transactions;
    select count(*) into :n_missing_counts trimmed from fraud.txn_features
        where missing(txns_prev_24h) or missing(logins_prev_1h) or missing(prior_debit_count);
quit;

data _null_;
    if &n_features ne &n_transactions then
        put "ERROR: txn_features has &n_features rows, fact_transactions has &n_transactions";
    else if &n_distinct ne &n_features then
        put "ERROR: duplicate txn_id values in txn_features";
    else if &n_missing_counts > 0 then
        put "ERROR: &n_missing_counts rows have missing count features (a join failed)";
    else put "NOTE: Feature table checks passed: &n_features rows, one per transaction.";
run;


/*---------------------------------------------------------------- 5. totals for the parallel run with Python */
ods pdf file="&reports/feature_report.pdf" style=journal startpage=no;

title "Feature totals (parallel run: compare with python/feature_reference.py)";
ods output summary=work.totals_wide;
proc means data=fraud.txn_features n sum stackodsoutput;
    var minutes_since_prev_txn txns_prev_1h txns_prev_24h declined_prev_24h
        debit_amount_prev_24h prior_debit_count prior_avg_debit amount_to_avg drain_ratio
        new_payee new_merchant new_device_for_account
        logins_prev_1h failed_logins_prev_1h foreign_logins_prev_1h unknown_device_logins_prev_1h
        is_night is_weekend;
run;

data work.feature_totals;
    set work.totals_wide (keep=variable n sum);
    length feature $32;
    feature = lowcase(variable);
    total = round(sum, 0.0001);
    format total best32.;                        /* full precision in the CSV, not the default 12 digits */
    keep feature n total;
run;

proc export data=work.feature_totals outfile="&reports/feature_totals_sas.csv" dbms=csv replace;
run;


/*---------------------------------------------------------------- 6. do the features separate fraud? */
title "Feature averages: confirmed fraud (1) vs not confirmed (0), in-scope transactions";
proc means data=fraud.txn_features mean maxdec=3 nonobs;
    where in_scope = 1;
    class txn_type fraud_label;
    var new_payee new_merchant new_device_for_account device_known
        unknown_device_logins_prev_1h failed_logins_prev_1h foreign_logins_prev_1h
        txns_prev_1h declined_prev_24h amount_to_avg drain_ratio is_night;
run;

title;
ods pdf close;
%put NOTE: Feature report saved to &reports/feature_report.pdf and totals to &reports/feature_totals_sas.csv;
