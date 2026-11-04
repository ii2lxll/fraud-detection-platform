/*=====================================================================
  04_rules_baseline.sas
  The rules-based baseline: simple IF-THEN fraud rules, like the ones most
  banks start with. Every model built later has to BEAT these numbers.

  How the rules are measured
    Population      in-scope transactions (transfers out, card purchases) that succeeded.
                    A declined payment loses no money and the bank gets no claim about it,
                    so it has no reliable label.
    Maturity window only transactions at least &maturity_days days before the end of the data.
                    Fraud is reported days to weeks late (02_eda.sas, chart 9), so recent
                    transactions would wrongly look "safe" and every rule would look worse.
    Metrics         alerts per day       workload for the investigation team (capacity: 50/day)
                    precision            share of alerts that are confirmed fraud
                    recall               share of confirmed fraud the rule catches
                    value detection      share of the fraud MONEY the rule catches

  Parallel run: python/rules_reference.py applies the same rules to the same data.
  Its comparison with reports/rules_results_sas.csv must show MATCH for every rule.

  Run 00_setup.sas and 03_features.sas first.
=====================================================================*/

%let maturity_days    = 30;
%let capacity_per_day = 50;


/*---------------------------------------------------------------- 1. apply the rules
  A comparison with a missing value is simply false in SAS (e.g. device_known = 0
  when device_known is missing), so a rule never fires on missing information. */
data work.rule_hits;
    set fraud.txn_features;
    where in_scope = 1 and status = 'SUCCESS' and days_to_data_end >= &maturity_days;

    is_transfer = (txn_type = 'TRANSFER_OUT');
    is_card     = (txn_type = 'CARD_PURCHASE');

    /* account takeover: a criminal on their own device paying a new payee */
    unknown_device_new_payee = is_transfer and device_known = 0 and new_payee = 1;
    /* account takeover: password guessing from abroad just before the transfer */
    login_attack             = is_transfer and failed_logins_prev_1h >= 2 and foreign_logins_prev_1h >= 1;
    /* account takeover: emptying the account at night */
    night_drain              = is_transfer and is_night = 1 and drain_ratio >= 0.3;
    /* scam: an unusually large payment to someone never paid before */
    large_new_payee          = is_transfer and new_payee = 1 and amount_to_avg >= 5;
    /* card fraud: a burst of purchases at a new high-risk merchant */
    risky_merchant_burst     = is_card and merchant_risk_tier = 'HIGH' and new_merchant = 1 and txns_prev_1h >= 2;
    /* card fraud: an unusually large purchase at a foreign high-risk merchant */
    foreign_risky_spike      = is_card and merchant_risk_tier = 'HIGH' and merchant_is_foreign = 1
                               and amount_to_avg >= 3;

    any_rule = (unknown_device_new_payee or login_attack or night_drain or
                large_new_payee or risky_merchant_burst or foreign_risky_spike);
run;

proc sql;
    create table work.eval_totals as
    select count(*)                  as eval_transactions,
           count(distinct date_key)  as eval_days,
           sum(fraud_label)          as eval_fraud,
           sum(fraud_label * amount) as eval_fraud_amount
    from work.rule_hits;
quit;

title "Evaluation population (transactions at least &maturity_days days old)";
proc print data=work.eval_totals noobs;
    format eval_transactions comma10. eval_fraud_amount dollar14.;
run;


/*---------------------------------------------------------------- 2. measure every rule
  The macro writes one SELECT per rule; UNION ALL stacks them into one table.
  (Descriptions must not contain commas: a comma separates macro arguments.) */
%macro measure(rule, description);
    select "&rule"                                        as rule length=32,
           "&description"                                 as description length=90,
           sum(r.&rule)                                   as alerts,
           calculated alerts / max(t.eval_days)           as alerts_per_day,
           sum(r.&rule * r.fraud_label)                   as fraud_caught,
           calculated fraud_caught / calculated alerts    as precision,
           calculated fraud_caught / max(t.eval_fraud)    as recall,
           sum(r.&rule * r.fraud_label * r.amount)        as fraud_amount_caught,
           calculated fraud_amount_caught / max(t.eval_fraud_amount) as value_detection
    from work.rule_hits as r, work.eval_totals as t
%mend measure;

proc sql;
    create table work.rule_results as
    %measure(unknown_device_new_payee, Transfer from an unknown device to a new payee)
    union all
    %measure(login_attack,             Transfer after 2+ failed logins and a foreign login in the hour before)
    union all
    %measure(night_drain,              Night-time transfer taking 30%+ of the balance)
    union all
    %measure(large_new_payee,          New payee and 5x the usual amount)
    union all
    %measure(risky_merchant_burst,     Card burst: new high-risk merchant and 2+ transactions in the hour before)
    union all
    %measure(foreign_risky_spike,      Card: foreign high-risk merchant and 3x the usual amount)
    union all
    %measure(any_rule,                 ANY of the rules above (the full rule set))
    ;
quit;

ods pdf file="&reports/rules_baseline_report.pdf" style=journal startpage=no;

title "Rules baseline: performance of each rule (confirmed fraud only)";
proc print data=work.rule_results noobs label;
    var rule alerts alerts_per_day fraud_caught precision recall value_detection;
    format alerts fraud_caught comma8. alerts_per_day 8.1 precision recall value_detection percent9.1;
    label alerts_per_day = "Alerts per day" fraud_caught = "Fraud caught" value_detection = "Fraud value caught";
run;

title "Rule descriptions";
proc print data=work.rule_results noobs;
    var rule description;
run;


/*---------------------------------------------------------------- 3. against the business targets */
data work.target_check;
    set work.rule_results (where=(rule = 'any_rule'));
    length target $48 actual $16 result $4;
    target = 'Fraud detection rate (recall) at least 70%';
    actual = put(recall, percent9.1);          result = ifc(recall >= 0.70, 'PASS', 'FAIL');          output;
    target = 'Value detection rate at least 80%';
    actual = put(value_detection, percent9.1); result = ifc(value_detection >= 0.80, 'PASS', 'FAIL'); output;
    target = 'Alert precision at least 10%';
    actual = put(precision, percent9.1);       result = ifc(precision >= 0.10, 'PASS', 'FAIL');       output;
    target = "At most &capacity_per_day alerts per day";
    actual = put(alerts_per_day, 8.1);         result = ifc(alerts_per_day <= &capacity_per_day, 'PASS', 'FAIL'); output;
    target = 'At most 10 false alerts per fraud caught';
    false_per_fraud = (alerts - fraud_caught) / fraud_caught;
    actual = put(false_per_fraud, 8.1);        result = ifc(false_per_fraud <= 10, 'PASS', 'FAIL');   output;
    keep target actual result;
run;

title "Full rule set against the business targets (docs/business_case.md)";
proc print data=work.target_check noobs;
run;


/*---------------------------------------------------------------- 4. the trade-off, in one picture */
title "Each rule: share of fraud caught vs share of alerts that are fraud";
proc sgplot data=work.rule_results;
    scatter x=recall y=precision / datalabel=rule markerattrs=(symbol=circlefilled size=10 color=cx2F5D8C);
    xaxis label="Recall (share of confirmed fraud caught)" min=0 max=1 grid;
    yaxis label="Precision (share of alerts that are fraud)" min=0 max=1 grid;
run;

title;
ods pdf close;


/*---------------------------------------------------------------- 5. export for the parallel run with Python */
data work.rules_export;
    set work.rule_results;
    format fraud_amount_caught best32.;
    keep rule alerts fraud_caught fraud_amount_caught;
run;

proc export data=work.rules_export outfile="&reports/rules_results_sas.csv" dbms=csv replace;
run;

%put NOTE: Rules baseline saved to &reports/rules_baseline_report.pdf and &reports/rules_results_sas.csv;
