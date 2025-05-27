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
    2. Compte par défaut du groupe d'articles (premier compte trouvé dans special_item_accountancy_code_details)
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
            # ✅ SUPPRIMÉ: frappe.msgprint(f"🎯 Priorité 1 - Compte article: {doc_item.get(account_field)}")
            frappe.logger().info(f"Priorité 1 - Compte article sélectionné: {doc_item.get(account_field)}")
            return doc_item.get(account_field)
        
        # PRIORITÉ 2: Compte par défaut du groupe d'articles - CORRECTION DÉFINITIVE
        if doc_item.item_group:
            try:
                item_group_doc = frappe.get_doc("Item Group", doc_item.item_group)
                
                # ✅ ACCÈS À LA TABLE PERSONNALISÉE
                if hasattr(item_group_doc, 'special_item_accountancy_code_details') and item_group_doc.special_item_accountancy_code_details:
                    # Prendre le PREMIER compte trouvé (compte par défaut du groupe)
                    first_detail = item_group_doc.special_item_accountancy_code_details[0]
                    
                    group_account = None
                    if type_thirdparty == "Customer":
                        group_account = first_detail.get("compte_de_produits")
                    else:
                        group_account = first_detail.get("compte_de_charges")
                    
                    if group_account:
                        # ✅ SUPPRIMÉ: frappe.msgprint(f"🎯 Priorité 2 - Compte groupe: {group_account}")
                        frappe.logger().info(f"Priorité 2 - Compte groupe sélectionné: {group_account}")
                        return group_account
                    
            except Exception as e:
                frappe.log_error(f"Erreur accès groupe d'articles: {str(e)}")
        
        # PRIORITÉ 3: Compte par défaut de la société
        default_company_account = get_company_default_account(company, type_thirdparty)
        if default_company_account:
            # ✅ SUPPRIMÉ: frappe.msgprint(f"🎯 Priorité 3 - Compte société: {default_company_account}")
            frappe.logger().info(f"Priorité 3 - Compte société sélectionné: {default_company_account}")
            return default_company_account
        
        # PRIORITÉ 4: Logique ancienne basée sur la catégorie comptable tiers (dernier recours)
        legacy_account = get_correct_default_account_legacy(third_party, type_thirdparty, item_code)
        if legacy_account:
            # ✅ SUPPRIMÉ: frappe.msgprint(f"⚠️ Priorité 4 - Compte tiers (dernier recours): {legacy_account}")
            frappe.logger().warning(f"Priorité 4 - Compte tiers utilisé en dernier recours: {legacy_account}")
            return legacy_account
        
        # PRIORITÉ 5: Compte de fallback système
        fallback_account = get_system_fallback_account(company, type_thirdparty)
        if fallback_account:
            # ✅ SUPPRIMÉ: frappe.msgprint(f"🚨 Priorité 5 - Compte fallback: {fallback_account}")
            frappe.logger().error(f"Priorité 5 - Compte fallback utilisé: {fallback_account}")
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
        for itm in doc.items:
            new_account = get_correct_default_account_new_logic(
                doc.supplier, "Supplier", itm.item_code, doc.company
            )
            if new_account:
                itm.expense_account = new_account

    # Pour les factures de vente
    if doc.get("doctype") in sales_doctypes:
        for itm in doc.items:
            new_account = get_correct_default_account_new_logic(
                doc.customer, "Customer", itm.item_code, doc.company
            )
            if new_account:
                itm.income_account = new_account


# FONCTION DE DEBUG SPÉCIALE POUR COMPRENDRE LA STRUCTURE
@frappe.whitelist()
def debug_item_group_structure(item_group_name):
    """
    Debug: Explorer la structure d'un groupe d'articles pour comprendre comment accéder aux comptes
    """
    try:
        item_group_doc = frappe.get_doc("Item Group", item_group_name)
        
        result = {
            "item_group_name": item_group_name,
            "all_fields": [],
            "item_defaults": [],
            "custom_fields": [],
            "debug_info": []
        }
        
        # Lister tous les champs du document
        for field in item_group_doc.meta.fields:
            field_info = {
                "fieldname": field.fieldname,
                "fieldtype": field.fieldtype,
                "label": field.label,
                "value": item_group_doc.get(field.fieldname) if hasattr(item_group_doc, field.fieldname) else None
            }
            result["all_fields"].append(field_info)
            
            # Chercher les champs liés aux comptes
            if "account" in field.fieldname.lower() or "income" in field.fieldname.lower() or "expense" in field.fieldname.lower():
                result["custom_fields"].append(field_info)
        
        # Vérifier les Item Defaults
        company = frappe.defaults.get_user_default("Company")
        item_defaults = frappe.db.sql("""
            SELECT *
            FROM `tabItem Default`
            WHERE parent = %s AND parenttype = 'Item Group' AND company = %s
        """, (item_group_name, company), as_dict=True)
        
        result["item_defaults"] = item_defaults
        
        # Informations de debug
        result["debug_info"].append(f"Nombre de champs: {len(result['all_fields'])}")
        result["debug_info"].append(f"Champs avec 'account': {len(result['custom_fields'])}")
        result["debug_info"].append(f"Item Defaults trouvés: {len(item_defaults)}")
        
        return result
        
    except Exception as e:
        return {"error": str(e)}


# FONCTION DE TEST POUR VALIDATION - SANS POP-UP
@frappe.whitelist()
def test_nouveau_code(item_code, customer=None):
    """
    Test de la nouvelle logique avec un article spécifique - VERSION SILENCIEUSE
    """
    try:
        company = frappe.defaults.get_user_default("Company")
        
        if not customer:
            customers = frappe.get_all("Customer", limit=1, fields=["name"])
            customer = customers[0].name if customers else None
        
        result = get_correct_default_account_new_logic(
            customer, "Customer", item_code, company
        )
        
        return {
            "item_code": item_code,
            "customer": customer,
            "company": company,
            "selected_account": result,
            "success": True,
            "message": f"Compte sélectionné: {result}" if result else "Aucun compte trouvé"
        }
        
    except Exception as e:
        return {
            "item_code": item_code,
            "error": str(e),
            "success": False
        }