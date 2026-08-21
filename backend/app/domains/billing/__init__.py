"""Billing domain — plans, subscriptions, invoices, payments.

Keep this package initializer side-effect free. Dedicated workers import billing
repositories and services without loading the HTTP application or its database
credentials.
"""
