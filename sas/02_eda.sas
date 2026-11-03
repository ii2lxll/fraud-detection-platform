/*=====================================================================
  02_eda.sas
  Exploratory data analysis: what does fraud look like in this bank?

  Every question below is answered with a table or chart, and everything
  is also saved to ~/fraud_platform/reports/eda_report.pdf.

  Remember: fraud_label = 1 means CONFIRMED by the bank's investigations.
  fraud_label = 0 means "not confirmed": genuine, OR fraud nobody has
  reported yet (see docs/adr/0003 in the GitHub repository).

  Run 00_setup.sas and 01_import_mart.sas first.
=====================================================================*/

ods pdf file="&reports/eda_report.pdf" style=journal startpage=no;
ods graphics on / width=7in height=3.5in;

/*---------------------------------------------------------------- 1. how rare is fraud? */
title "1. How many transactions are confirmed fraud?";
proc freq data=fraud.fact_transactions;
    tables fraud_label claim_status / missing nocum;
run;

/*---------------------------------------------------------------- 2. device signal */
title "2. Mobile and web: fraud rate on registered vs unknown devices";
proc sql;
    select channel,
           device_known,
           count(*)          as transactions format=comma10.,
           sum(fraud_label)  as confirmed_fraud,
           mean(fraud_label) as fraud_rate     format=percent10.3
    from fraud.fact_transactions
    where channel in ('MOBILE', 'WEB')
    group by channel, device_known
    order by channel, device_known;
quit;

/*---------------------------------------------------------------- 3. transaction type */
title "3. Fraud rate by transaction type";
proc sql;
    select txn_type,
           count(*)          as transactions format=comma10.,
           sum(fraud_label)  as confirmed_fraud,
           mean(fraud_label) as fraud_rate     format=percent10.3
    from fraud.fact_transactions
    group by txn_type
    order by fraud_rate desc;
quit;

/*---------------------------------------------------------------- 4. time of day */
proc sql;
    create table work.by_hour as
    select hour_of_day,
           count(*)          as transactions,
           mean(fraud_label) as fraud_rate format=percent10.3
    from fraud.fact_transactions
    group by hour_of_day;
quit;

title "4. Fraud rate by hour of day";
proc sgplot data=work.by_hour;
    vbar hour_of_day / response=fraud_rate fillattrs=(color=cx2F5D8C);
    xaxis label="Hour of day";
    yaxis label="Confirmed fraud rate" grid;
run;

/*---------------------------------------------------------------- 5. card purchases: merchant risk */
title "5. Card purchases: fraud rate by merchant risk tier and location";
proc sql;
    select m.risk_tier,
           case when m.is_foreign = 1 then 'Foreign' else 'Home' end as merchant_location,
           count(*)            as transactions format=comma10.,
           sum(t.fraud_label)  as confirmed_fraud,
           mean(t.fraud_label) as fraud_rate     format=percent10.3
    from fraud.fact_transactions as t
    inner join fraud.dim_merchant as m on t.merchant_key = m.merchant_key
    where t.txn_type = 'CARD_PURCHASE'
    group by m.risk_tier, calculated merchant_location
    order by fraud_rate desc;
quit;

/*---------------------------------------------------------------- 6. amounts */
title "6. Amounts: confirmed fraud vs everything else";
proc means data=fraud.fact_transactions n mean median p95 max maxdec=2;
    class fraud_label;
    var amount;
run;

/*---------------------------------------------------------------- 7. who is targeted by scams? */
title "7. Scam claims by age band of the victim";
proc sql;
    select c.age_band,
           count(distinct c.customer_key) as customers,
           count(distinct case when f.claim_type = 'SCAM_PAYMENT' then f.customer_key end)
                                          as scam_victims,
           calculated scam_victims / calculated customers as victim_rate format=percent10.2
    from fraud.dim_customer as c
    left join fraud.fact_fraud_claims as f on f.customer_key = c.customer_key
    group by c.age_band
    order by c.age_band;
quit;

/*---------------------------------------------------------------- 8. reporting delay */
title "8. How many days until fraud is reported?";
proc means data=fraud.fact_fraud_claims n mean median p90 max maxdec=1;
    class claim_type;
    var days_to_report;
run;

/*---------------------------------------------------------------- 9. label maturity */
proc sql;
    create table work.by_week as
    select d.iso_week,
           count(*)           as transactions,
           sum(t.fraud_label) as confirmed_fraud
    from fraud.fact_transactions as t
    inner join fraud.dim_date as d on t.date_key = d.date_key
    group by d.iso_week;
quit;

title "9. Confirmed fraud by week: why the most recent weeks look 'safe'";
footnote "Fraud is reported days to weeks later, so recent weeks are not fully labelled yet.";
proc sgplot data=work.by_week;
    series x=iso_week y=confirmed_fraud / markers lineattrs=(color=cx2F5D8C thickness=2);
    xaxis label="ISO week of the transaction" integer;
    yaxis label="Confirmed fraud transactions" grid;
run;

title;
footnote;
ods pdf close;
%put NOTE: EDA report saved to &reports/eda_report.pdf;
