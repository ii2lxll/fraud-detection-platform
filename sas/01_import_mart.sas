/*=====================================================================
  01_import_mart.sas
  Import the 7 mart CSV files into SAS datasets in the FRAUD library,
  then check every dataset against export_manifest.json.

  Why DATA steps instead of PROC IMPORT?
  PROC IMPORT GUESSES each column's type and length from the first rows.
  A guess can be wrong (a text column that starts empty, a long name that
  appears late) and changes between runs. Explicit DATA steps define every
  column, so the result is the same every time: what a production SAS
  program must guarantee.

  Options used on every INFILE:
    dsd        comma-separated, respects "quoted, values" and empty fields
    firstobs=2 skip the header row
    truncover  a short last line does not shift values into the wrong column

  Run 00_setup.sas first.
=====================================================================*/

/*---------------------------------------------------------------- dim_date */
data fraud.dim_date;
    infile "&exports/dim_date.csv" dsd firstobs=2 truncover;
    length day_name month_name $3;
    informat calendar_date yymmdd10.;
    format   calendar_date date9.;
    input date_key calendar_date day_of_week day_name $ is_weekend day_of_month
          iso_week month_number month_name $ quarter year;
run;

/*---------------------------------------------------------------- dim_customer */
data fraud.dim_customer;
    infile "&exports/dim_customer.csv" dsd firstobs=2 truncover;
    length customer_id $10 age_band $7 segment $14 income_band $9 kyc_risk_rating $6
           preferred_channel $6 state $10;
    informat customer_since yymmdd10.;
    format   customer_since date9.;
    input customer_key customer_id $ age age_band $ segment $ income_band $
          kyc_risk_rating $ preferred_channel $ state $ customer_since tenure_days;
run;

/*---------------------------------------------------------------- dim_account */
data fraud.dim_account;
    infile "&exports/dim_account.csv" dsd firstobs=2 truncover;
    length account_id $10 account_type $17 status $7;
    informat open_date close_date mule_confirmed_date yymmdd10.;
    format   open_date close_date mule_confirmed_date date9. opening_balance comma14.2;
    input account_key account_id $ customer_key account_type $ status $ open_date close_date
          account_age_days opening_balance has_active_card is_confirmed_mule mule_confirmed_date;
run;

/*---------------------------------------------------------------- dim_merchant */
data fraud.dim_merchant;
    infile "&exports/dim_merchant.csv" dsd firstobs=2 truncover;
    length merchant_id $8 merchant_name $60 category $16 channel $8 country $4 risk_tier $6;
    input merchant_key merchant_id $ merchant_name $ category $ mcc channel $ country $
          is_foreign risk_tier $;
run;

/*---------------------------------------------------------------- fact_transactions */
data fraud.fact_transactions;
    infile "&exports/fact_transactions.csv" dsd firstobs=2 truncover;
    length txn_id $12 txn_type $15 direction $2 channel $8 status $8
           counterparty_account $16 counterparty_bank $16 device_id $36 claim_status $9;
    informat txn_timestamp anydtdtm19. first_reported_date confirmed_date yymmdd10.;
    format   txn_timestamp datetime19. first_reported_date confirmed_date date9.
             amount signed_amount balance_before balance_after comma14.2;
    input txn_id $ date_key txn_timestamp hour_of_day account_key customer_key merchant_key
          txn_type $ direction $ channel $ amount signed_amount balance_before balance_after
          status $ is_declined counterparty_account $ counterparty_bank $ is_internal_transfer
          device_id $ device_known arrived_late fraud_label claim_status $
          first_reported_date confirmed_date;
    label fraud_label  = "1 = confirmed fraud (as known to the bank)"
          device_known = "Mobile/web: 1 = device registered to this customer";
run;

/*---------------------------------------------------------------- fact_logins */
data fraud.fact_logins;
    infile "&exports/fact_logins.csv" dsd firstobs=2 truncover;
    length event_id $12 session_id $36 device_id $36 channel $6 ip_address $15
           ip_country $2 result $7 failure_reason $14;
    informat event_timestamp anydtdtm19.;
    format   event_timestamp datetime19.;
    input event_id $ date_key event_timestamp hour_of_day customer_key session_id $
          device_id $ device_known channel $ ip_address $ ip_country $ is_foreign_ip
          result $ is_failed failure_reason $;
run;

/*---------------------------------------------------------------- fact_fraud_claims */
data fraud.fact_fraud_claims;
    infile "&exports/fact_fraud_claims.csv" dsd firstobs=2 truncover;
    length report_id $10 bank_case_ref $9 txn_id $12 claim_type $21 report_channel $13
           claim_status $9;
    informat reported_date resolved_date yymmdd10.;
    format   reported_date resolved_date date9. amount comma14.2;
    input report_id $ bank_case_ref $ reported_date_key reported_date resolved_date
          account_key customer_key txn_id $ claim_type $ report_channel $ amount
          claim_status $ days_to_report days_to_resolve;
run;

/*---------------------------------------------------------------- check against the manifest
  export_manifest.json lists every file with its row count. SAS reads JSON
  with the JSON libname engine: the "files" array becomes dataset MANIFEST.FILES. */
filename manifest "&exports/export_manifest.json";
libname manifest json fileref=manifest;

proc sql;
    create table work.import_check as
    select m.file,
           m.rows   as expected_rows,
           d.nobs   as imported_rows,
           case when m.rows = d.nobs then 'OK' else 'MISMATCH' end as result length=8
    from manifest.files as m
    left join dictionary.tables as d
      on d.libname = 'FRAUD' and d.memname = upcase(scan(m.file, 1, '.'));
quit;

title "Import check: every dataset against export_manifest.json";
proc print data=work.import_check noobs;
run;
title;

/* write a clear ERROR line to the log for any mismatch, so it cannot be missed */
data _null_;
    set work.import_check end=last;
    if result ne 'OK' then do;
        put "ERROR: Import check failed for " file= expected_rows= imported_rows=;
        failed + 1;
    end;
    if last and failed = 0 then put "NOTE: Import check passed: all datasets match the manifest.";
run;

libname manifest clear;
filename manifest clear;
