# -*- coding: utf-8 -*-
# Copyright (c) 2021, scopen.fr and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import json

import frappe
from erpnext.stock.get_item_details import (
    get_item_details,
    process_args,
    purchase_doctypes,
    sales_doctypes,
)
from frappe import _
from six import string_types


@frappe.whitelist()
def get_item_details_custom(
    args, doc=None, for_validate=False, overwrite_warehouse=True
):
    # Standard feature
    out = get_item_details(args, doc, for_validate, overwrite_warehouse)

    # Process args and doc to use it as object
    args = process_args(args)
    if isinstance(doc, string_types):
        doc = json.loads(doc)

    # Deal with tax code selling or buying
    transaction_type = None
    type_thirdparty = None
    if doc:
        if doc.get("doctype") in purchase_doctypes:
            transaction_type = "Achat"
            type_thirdparty = "Supplier"
        if doc.get("doctype") in sales_doctypes:
            transaction_type = "Vente"
            type_thirdparty = "Customer"

    # By default we don't know what we are working on
    third_party = None
    if args.customer is not None:
        third_party = args.customer

    if args.supplier is not None:
        third_party = args.supplier

    # On Quotation there is no accountancy code
    if doc and doc.get("doctype") == "Quotation":
        type_thirdparty = None

    if type_thirdparty is not None and third_party is not None:
        # NOUVELLE LOGIQUE: Utiliser d'abord les comptes par défaut des produits
        account = get_correct_default_account_new_logic(
            third_party, type_thirdparty, args.item_code, args.company
        )
        
        if transaction_type == "Vente" and account is not None:
            out.income_account = account
        if transaction_type == "Achat" and account is not None:
            out.expense_account = account

    return out


def get_correct_default_account_new_logic(third_party, type_thirdparty, item_code, company):
    """
    NOUVELLE LOGIQUE DE PRIORITÉ:
    1. Compte spécifique de l'article (income_account/expense_account sur l'article)
    2. Compte par défaut du groupe d'articles 
    3. Compte par défaut de la société
    4. Logique ancienne basée sur la catégorie comptable tiers (dernier recours)
    5. Compte de fallback système
    """
    
    if not third_party or not item_code:
        return None
        
    try:
        doc_item = frappe.get_doc("Item", item_code)
        account_field = "income_account" if type_thirdparty == "Customer" else "expense_account"
        
        # PRIORITÉ 1: Compte spécifique de l'article
        if doc_item.get(account_field):
            frappe.msgprint(f"🎯 Priorité 1 - Compte article: {doc_item.get(account_field)}")
            return doc_item.get(account_field)
        
        # PRIORITÉ 2: Compte par défaut du groupe d'articles - CORRECTION ICI
        if doc_item.item_group:
            try:
                item_group_doc = frappe.get_doc("Item Group", doc_item.item_group)
                
                # ✅ CORRECTION: Accès direct aux bons noms de champs
                if type_thirdparty == "Customer":
                    group_account = item_group_doc.get("default_income_account")
                else:
                    group_account = item_group_doc.get("default_expense_account")
                
                if group_account:
                    frappe.msgprint(f"🎯 Priorité 2 - Compte groupe: {group_account}")
                    return group_account
                    
            except Exception as e:
                frappe.log_error(f"Erreur accès groupe d'articles: {str(e)}")
        
        # PRIORITÉ 3: Compte par défaut de la société
        default_company_account = get_company_default_account(company, type_thirdparty)
        if default_company_account:
            frappe.msgprint(f"🎯 Priorité 3 - Compte société: {default_company_account}")
            return default_company_account
        
        # PRIORITÉ 4: Logique ancienne basée sur la catégorie comptable tiers (dernier recours)
        legacy_account = get_correct_default_account_legacy(third_party, type_thirdparty, item_code)
        if legacy_account:
            frappe.msgprint(f"⚠️ Priorité 4 - Compte tiers (dernier recours): {legacy_account}")
            return legacy_account
        
        # PRIORITÉ 5: Compte de fallback système
        fallback_account = get_system_fallback_account(company, type_thirdparty)
        if fallback_account:
            frappe.msgprint(f"🚨 Priorité 5 - Compte fallback: {fallback_account}")
            return fallback_account
            
    except Exception as e:
        frappe.log_error(f"Erreur dans get_correct_default_account_new_logic: {str(e)}")
        
    return None

def get_company_default_account(company, type_thirdparty):
    """
    Récupère le compte par défaut de la société
    """
    try:
        company_doc = frappe.get_doc("Company", company)
        
        if type_thirdparty == "Customer":
            # Compte de revenus par défaut
            return (company_doc.get("default_income_account") or 
                   frappe.db.get_single_value("Selling Settings", "default_income_account"))
        else:
            # Compte de charges par défaut  
            return (company_doc.get("default_expense_account") or
                   frappe.db.get_single_value("Buying Settings", "default_expense_account"))
    except:
        return None


def get_system_fallback_account(company, type_thirdparty):
    """
    Compte de fallback système si rien d'autre n'est trouvé
    """
    try:
        if type_thirdparty == "Customer":
            # Chercher un compte de revenus générique
            accounts = frappe.db.sql("""
                SELECT name FROM `tabAccount` 
                WHERE company = %s 
                AND account_type = 'Income Account' 
                AND is_group = 0
                ORDER BY name
                LIMIT 1
            """, (company,))
        else:
            # Chercher un compte de charges générique
            accounts = frappe.db.sql("""
                SELECT name FROM `tabAccount` 
                WHERE company = %s 
                AND account_type = 'Expense Account' 
                AND is_group = 0
                ORDER BY name
                LIMIT 1
            """, (company,))
            
        return accounts[0][0] if accounts else None
    except:
        return None


def get_correct_default_account_legacy(third_party, type_thirdparty, item_code):
    """
    ANCIENNE LOGIQUE basée sur la catégorie comptable tiers
    Utilisée maintenant seulement en dernier recours
    """
    if third_party is None:
        return None
        
    try:
        doc_thirdparty = frappe.get_doc(type_thirdparty, third_party)
        categ_compta_thirdparty = doc_thirdparty.categorie_comptable_tiers
        
        if not categ_compta_thirdparty:
            return None
            
        doc_item = frappe.get_doc("Item", item_code)
        account = None

        # 1. Paramètres spécifiques de l'article (PRIORITÉ MAXIMALE dans l'ancienne logique)
        if len(doc_item.special_item_accountancy_code_details) != 0:
            for detail in doc_item.special_item_accountancy_code_details:
                if detail.categorie_comptable_tiers == categ_compta_thirdparty:
                    if type_thirdparty == "Customer":
                        account = detail.compte_de_produits
                    if type_thirdparty == "Supplier":
                        account = detail.compte_de_charges
                    if account:
                        return account

        # 2. Paramètres du groupe d'articles
        for item_group_categ in frappe.db.get_all(
            doctype="Categorie comptable Tiers et code comptable Produit",
            as_list=True,
            filters={"parent": doc_item.item_group, "parenttype": "Item Group"},
        ):
            thirdparty_categ = frappe.get_doc(
                "Categorie comptable Tiers et code comptable Produit",
                item_group_categ[0],
            )
            if thirdparty_categ.categorie_comptable_tiers == categ_compta_thirdparty:
                if type_thirdparty == "Customer":
                    account = thirdparty_categ.compte_de_produits
                if type_thirdparty == "Supplier":
                    account = thirdparty_categ.compte_de_charges
                if account:
                    return account

        # 3. Paramètres globaux par défaut
        for thirdparty_setup_categ in frappe.db.get_all(
            doctype="Categorie comptable Tiers et code comptable Produit",
            as_list=True,
            filters={"parent": "Special Item Accountancy Code Default"},
        ):
            thirdparty_categ = frappe.get_doc(
                "Categorie comptable Tiers et code comptable Produit",
                thirdparty_setup_categ[0],
            )
            if thirdparty_categ.categorie_comptable_tiers == categ_compta_thirdparty:
                if type_thirdparty == "Customer":
                    account = thirdparty_categ.compte_de_produits
                if type_thirdparty == "Supplier":
                    account = thirdparty_categ.compte_de_charges
                if account:
                    return account

        return account
        
    except Exception as e:
        frappe.log_error(f"Erreur dans get_correct_default_account_legacy: {str(e)}")
        return None


# FONCTION ORIGINALE CONSERVÉE POUR RÉTROCOMPATIBILITÉ
def get_correct_default_account(third_party, type_thirdparty, item_code):
    """
    Fonction originale conservée pour rétrocompatibilité
    Redirige vers la nouvelle logique
    """
    # Essayer de récupérer la société depuis le contexte
    company = frappe.defaults.get_user_default("Company")
    
    return get_correct_default_account_new_logic(
        third_party, type_thirdparty, item_code, company
    )


@frappe.whitelist()
def get_correct_default_account_validate(doc, method):
    """
    Hook de validation - LOGIQUE MODIFIÉE
    """
    if not doc:
        return
        
    # Pour les factures d'achat
    if doc.get("doctype") in purchase_doctypes:
        # CHANGEMENT MAJEUR: On ne force plus la catégorie comptable tiers obligatoire
        # supplier = frappe.get_doc("Supplier", doc.supplier)
        # if (supplier.categorie_comptable_tiers is None) or (
        #     supplier.categorie_comptable_tiers == ""
        # ):
        #     frappe.throw(_("Supplier accountancy category is missing"))
            
        for itm in doc.items:
            new_account = get_correct_default_account_new_logic(
                doc.supplier, "Supplier", itm.item_code, doc.company
            )
            if new_account:
                itm.expense_account = new_account

    # Pour les factures de vente
    if doc.get("doctype") in sales_doctypes:
        # CHANGEMENT MAJEUR: On ne force plus la catégorie comptable tiers obligatoire
        # customer = frappe.get_doc("Customer", doc.customer)
        # if (customer.categorie_comptable_tiers is None) or (
        #     customer.categorie_comptable_tiers == ""
        # ):
        #     frappe.throw(_("Customer accountancy category is missing"))
            
        for itm in doc.items:
            new_account = get_correct_default_account_new_logic(
                doc.customer, "Customer", itm.item_code, doc.company
            )
            if new_account:
                itm.income_account = new_account