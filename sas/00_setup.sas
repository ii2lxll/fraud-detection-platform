/*=====================================================================
  00_setup.sas
  Run this FIRST in every SAS session. It defines where the files are
  and creates the FRAUD library where all project datasets are stored.

  Folder layout in your SAS OnDemand home (~):
      ~/fraud_platform/exports    the CSV files from python/export_mart.py (you upload these)
      ~/fraud_platform/sasdata    SAS datasets created by these programs (created automatically)
      ~/fraud_platform/reports    PDF reports created by these programs (created automatically)
=====================================================================*/

%let root    = ~/fraud_platform;
%let exports = &root/exports;
%let reports = &root/reports;

options dlcreatedir          /* LIBNAME creates its folder if it does not exist */
        validvarname=v7      /* standard SAS variable names                     */
        nofmterr;            /* do not stop if a user-defined format is missing */

libname fraud "&root/sasdata";

/* create the reports folder the same way, then release the temporary library */
libname _rep "&reports";
libname _rep clear;

%put NOTE: Setup complete. Exports folder: &exports;
