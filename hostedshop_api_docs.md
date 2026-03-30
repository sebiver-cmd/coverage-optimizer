# HostedShop SOAP API Documentation

> **Source:** Official HostedShop API — <https://api.hostedshop.io/doc/Hosted%20Solution%20API/>
>
> **WSDL endpoint:** `https://api.hostedshop.dk/service.wsdl`

This document describes the HostedShop (DanDomain) SOAP API types and
operations used by the **Coverage Optimizer** application.

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Output Field Configuration](#2-output-field-configuration)
3. [Product (GET response)](#3-product-get-response)
4. [ProductVariant (GET response)](#4-productvariant-get-response)
5. [ProductUpdate (for Product_Update)](#5-productupdate-for-product_update)
6. [ProductVariantUpdate (for Product_UpdateVariant)](#6-productvariantupdate-for-product_updatevariant)
7. [Key API Operations](#7-key-api-operations)
8. [Field Name Reference](#8-field-name-reference)

---

## 1. Authentication

### Solution_Connect

Authenticates the SOAP session. Must be called before any other
operation.

| Parameter  | Type   | Description                         |
|------------|--------|-------------------------------------|
| `Username` | string | API employee email                  |
| `Password` | string | API employee password               |

**Setup:** In the DanDomain admin panel go to *Settings → API: SOAP*
and enable API access.  Under *Settings → Employees* create an API
user (email + password).

---

## 2. Output Field Configuration

These operations control **which fields appear in GET responses**.
They are *not* partial-update methods — they only affect the shape of
data returned by subsequent GET calls.

### Product_SetFields

Configures which properties are included in `Product` objects returned
by `Product_GetAll`, `Product_GetByItemNumber`, `Product_GetAllWithLimit`,
`Product_GetByBrand`, etc.

| Parameter | Type   | Description                                      |
|-----------|--------|--------------------------------------------------|
| `Fields`  | string | **Comma-separated** field names (e.g. `"Id,ItemNumber,Price,BuyingPrice,Variants"`) |

> **Important:** The `Fields` parameter must be a single
> comma-separated string — NOT an array.

### Product_SetVariantFields

Same concept as above, but controls the fields returned for
`ProductVariant` objects.

| Parameter | Type   | Description                                      |
|-----------|--------|--------------------------------------------------|
| `Fields`  | string | **Comma-separated** field names (e.g. `"Id,ItemNumber,Price,BuyingPrice"`) |

---

## 3. Product (GET response)

**Class:** `Product`
([docs](https://api.hostedshop.io/doc/Hosted%20Solution%20API/Product.html))

Returned by `Product_GetAll`, `Product_GetByItemNumber`,
`Product_GetAllWithLimit`, `Product_GetByBrand`, etc.

| Field                | Type                    | Description                                                                 |
|----------------------|-------------------------|-----------------------------------------------------------------------------|
| `Id`                 | int                     | Internal unique product ID (required for updates)                          |
| `ItemNumber`         | string                  | SKU / product number — often the unique identifier for ERP syncing         |
| `Title`              | string                  | Display name (language-dependent)                                          |
| `Price`              | double                  | Selling price (usually including VAT depending on shop settings)           |
| `BuyingPrice`        | double                  | Internal purchase / cost price                                             |
| `Producer`           | User (complex object)   | Brand / manufacturer (use `Producer.Company` for the brand name)           |
| `ProducerId`         | int                     | ID of the Producer / brand                                                 |
| `CategoryId`         | int                     | Primary category ID                                                        |
| `Online`             | boolean                 | Whether the product is an online (file-sale) product                       |
| `Status`             | boolean                 | Whether the product is visible in the shop                                 |
| `Stock`              | int                     | Physical inventory count                                                   |
| `Ean`                | string                  | EAN barcode number                                                         |
| `ItemNumberSupplier` | string                  | Supplier's item number                                                     |
| `Weight`             | double                  | Product weight (for shipping calculations)                                 |
| `VatGroup`           | VatGroup                | Tax group object                                                           |
| `VatGroupId`         | int                     | Tax group ID                                                               |
| `Discount`           | double                  | Discount amount                                                            |
| `DiscountType`       | string                  | `'p'` for percent, `'a'` for amount                                        |
| `GuidelinePrice`     | double                  | Guideline / recommended price                                              |
| `Variants`           | ProductVariant[]        | Array of variant objects                                                   |
| `VariantTypes`       | string                  | Variant type titles concatenated with `//`                                 |
| `Description`        | string                  | Product description (language-dependent)                                   |
| `DescriptionLong`    | string                  | Long description (language-dependent)                                      |
| `DescriptionShort`   | string                  | Short description (language-dependent)                                     |
| `DateCreated`        | string                  | Creation datetime                                                          |
| `DateUpdated`        | string                  | Last update datetime                                                       |
| `Sorting`            | int                     | Position among siblings                                                    |
| `MinAmount`          | int                     | Minimum order quantity                                                     |
| `DisableOnEmpty`     | boolean                 | Hide when out of stock                                                     |
| `Type`               | mixed                   | Product type                                                               |
| `TypeLabel`          | string                  | `'normal'`, `'file-sale'`, `'gift-card-with-code'`, `'gift-card-without-code'`, `'call-for-price'`, `'discontinued'` |
| `ProductUrl`         | string                  | Product URL                                                                |
| `Pictures`           | ProductPicture[]        | Product images                                                             |
| `RelatedProductIds`  | int[]                   | IDs of related products                                                    |
| `SecondaryCategories`| Category[]              | Additional categories                                                      |
| `SecondaryCategoryIds`| int[]                  | IDs of additional categories                                               |
| `Tags`               | ProductTag[]            | Product tags                                                               |
| `CustomData`         | ProductCustomData[]     | Custom data fields                                                         |
| `Additionals`        | ProductAdditionalType[] | Additional product types                                                   |
| `AutoStock`          | AutoStock               | Automatic stock control (`1`=true, `0`=false, blank=inherit)               |
| `CallForPrice`       | boolean                 | "Call for Price" flag                                                      |
| `FocusCart`           | boolean                 | Display beneath cart for extra focus                                       |
| `FocusFrontpage`     | boolean                 | Display on frontpage for extra focus                                       |
| `OutOfStockBuy`      | OutOfStockBuy           | Out-of-stock purchase behaviour                                            |
| `LanguageISO`        | string                  | Language ISO code                                                          |
| `LanguageAccess`     | String[]                | Language/site access array                                                 |
| `Unit`               | ProductUnit             | Unit of measure                                                            |
| `UnitId`             | int                     | Unit of measure ID                                                         |
| `Url`                | string                  | External URL                                                               |
| `SeoTitle`           | string                  | SEO title                                                                  |
| `SeoDescription`     | string                  | SEO description                                                            |
| `SeoKeywords`        | string                  | SEO keywords                                                               |
| `SeoLink`            | string                  | SEO-friendly link                                                          |
| `SeoCanonical`       | string                  | SEO canonical URL                                                          |
| `RelationCode`       | string                  | Relation code                                                              |
| `StockLocationId`    | VatGroup                | Stock location ID (0 = default)                                            |
| `StockLow`           | int                     | Low stock warning threshold                                                |
| `StockLocations`     | ProductStockLocation[]  | Stock locations                                                            |
| `UserAccess`         | User[]                  | Users with access (empty = all)                                            |
| `UserAccessIds`      | int[]                   | IDs of users with access                                                   |
| `UserGroupAccess`    | UserGroup[]             | User groups with access (ID 0 = guests)                                    |
| `UserGroupAccessIds` | int[]                   | IDs of user groups with access                                             |
| `PacketProducts`     | PacketProductLine[]     | Packet product lines                                                       |
| `ExtraBuyRelations`  | ProductExtraBuyRelation[]| Extra-buy relations                                                       |
| `DiscountGroup`      | DiscountGroup           | Discount group object                                                      |
| `DiscountGroupId`    | int                     | Discount group ID                                                          |
| `Discounts`          | ProductDiscount[]       | Custom discounts                                                           |
| `Delivery`           | Delivery                | Delivery object                                                            |
| `DeliveryId`         | int                     | Delivery ID                                                                |
| `DeliveryTime`       | ProductDeliveryTime     | Delivery time (null for `standard`/`standard_lager`)                       |
| `DeliveryTimeId`     | int                     | Delivery time ID (0 = show stock, -1 = no info)                            |
| `CategorySortings`   | ProductCategorySorting[]| Category sorting combinations                                             |

---

## 4. ProductVariant (GET response)

**Class:** `ProductVariant`
([docs](https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductVariant.html))

Returned inside `Product.Variants` or by `Product_GetVariantsByItemNumber`.

| Field                | Type                           | Description                                                            |
|----------------------|--------------------------------|------------------------------------------------------------------------|
| `Id`                 | int                            | **Variant** ID (different from the parent Product ID)                  |
| `ProductId`          | int                            | ID of the parent product                                               |
| `ItemNumber`         | string                         | Variant-specific SKU (e.g. `16022-000-S`)                              |
| `Price`              | double                         | Selling price for this variant (overrides parent)                      |
| `BuyingPrice`        | double                         | Purchase / cost price for this variant                                 |
| `Stock`              | int                            | Inventory level for this variant                                       |
| `Status`             | boolean                        | Whether the variant is visible in the shop                             |
| `Title`              | string                         | Variant title (VariantTypeValue names joined with `//`)                |
| `Weight`             | double                         | Variant weight                                                         |
| `Ean`                | string                         | EAN barcode                                                            |
| `ItemNumberSupplier` | string                         | Supplier item number                                                   |
| `MinAmount`          | int                            | Minimum order quantity                                                 |
| `Discount`           | double                         | Discount amount                                                        |
| `DiscountType`       | string                         | `'p'` for percent, `'a'` for amount                                    |
| `DisableOnEmpty`     | boolean                        | Hide when out of stock                                                 |
| `Description`        | string                         | Description (language-dependent)                                       |
| `DescriptionLong`    | string                         | Long description (language-dependent)                                  |
| `VariantTypeValues`  | int[]                          | IDs of the variant attribute values (e.g. "Small", "Red")              |
| `Unit`               | ProductUnit                    | Unit of measure                                                        |
| `Sorting`            | int                            | Position among siblings                                                |
| `PictureId`          | int                            | *(Deprecated)* — use `PictureIds` instead                              |
| `PictureIds`         | int[]                          | IDs of variant pictures                                                |
| `StockLow`           | int                            | Low stock warning threshold                                            |
| `StockLocations`     | ProductVariantStockLocation[]  | Stock locations                                                        |
| `DeliveryTime`       | ProductDeliveryTime            | Delivery time                                                          |
| `DeliveryTimeId`     | int                            | Delivery time ID                                                       |

---

## 5. ProductUpdate (for Product_Update)

**Class:** `ProductUpdate`
([docs](https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductUpdate.html))

> Either `Id` or `ItemNumber` is required.  Only the fields that are
> **set** in the object will be updated — this enables partial updates.

| Field                | Type                    | Description                                                  |
|----------------------|-------------------------|--------------------------------------------------------------|
| `Id`                 | int                     | Product ID to update (required if `ItemNumber` not provided) |
| `ItemNumber`         | string                  | Product SKU (required if `Id` not provided)                  |
| `Price`              | double                  | Selling price                                                |
| `BuyingPrice`        | double                  | Purchase / cost price                                        |
| `Title`              | string                  | Product title                                                |
| `CategoryId`         | int                     | Primary category ID                                          |
| `ProducerId`         | int                     | Producer / brand ID                                          |
| `Stock`              | int                     | Inventory count                                              |
| `Status`             | boolean                 | Shop visibility                                              |
| `Online`             | boolean                 | Online product flag                                          |
| `Weight`             | double                  | Product weight                                               |
| `VatGroupId`         | int                     | Tax group ID                                                 |
| `Ean`                | string                  | EAN barcode                                                  |
| `ItemNumberSupplier` | string                  | Supplier item number                                         |
| `Discount`           | double                  | Discount amount                                              |
| `DiscountType`       | string                  | `'p'` for percent, `'a'` for amount                          |
| `DiscountGroupId`    | int                     | Discount group ID                                            |
| `GuidelinePrice`     | double                  | Guideline price                                              |
| `MinAmount`          | int                     | Minimum order quantity                                       |
| `DisableOnEmpty`     | boolean                 | Hide when out of stock                                       |
| `Description`        | string                  | Product description                                          |
| `DescriptionLong`    | string                  | Long description                                             |
| `DescriptionShort`   | string                  | Short description                                            |
| `CallForPrice`       | boolean                 | "Call for Price" flag                                        |
| `FocusCart`           | boolean                 | Display beneath cart                                         |
| `FocusFrontpage`     | boolean                 | Display on frontpage                                         |
| `AutoStock`          | AutoStock               | Automatic stock control                                      |
| `OutOfStockBuy`      | OutOfStockBuy           | Out-of-stock purchase behaviour                              |
| `DateCreated`        | string                  | Creation datetime                                            |
| `DateUpdated`        | string                  | Last update datetime                                         |
| `DeliveryId`         | int                     | Delivery ID                                                  |
| `DeliveryTimeId`     | int                     | Delivery time ID                                             |
| `Sorting`            | int                     | Sort position                                                |
| `Url`                | string                  | External URL                                                 |
| `UnitId`             | int                     | Unit of measure ID                                           |
| `LanguageISO`        | string                  | Language ISO code                                            |
| `LanguageAccess`     | String[]                | Language/site access                                         |
| `RelatedProducts`    | int[]                   | Related product IDs                                          |
| `SecondaryCategoryIds`| int[]                  | Secondary category IDs                                       |
| `CategorySortings`   | ProductCategorySorting[]| Category sorting                                             |
| `UserAccessIds`      | int[]                   | User access IDs                                              |
| `UserGroupAccessIds` | int[]                   | User group access IDs (0 = guests)                           |
| `SeoTitle`           | string                  | SEO title                                                    |
| `SeoDescription`     | string                  | SEO description                                              |
| `SeoKeywords`        | string                  | SEO keywords                                                 |
| `SeoLink`            | string                  | SEO link                                                     |
| `SeoCanonical`       | string                  | SEO canonical URL                                            |
| `TypeLabel`          | string                  | Product type label                                           |

---

## 6. ProductVariantUpdate (for Product_UpdateVariant)

**Class:** `ProductVariantUpdate`
([docs](https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductVariantUpdate.html))

> Either `Id` or `ItemNumber` is required.  Only the fields that are
> **set** in the object will be updated.

| Field                | Type   | Description                                                  |
|----------------------|--------|--------------------------------------------------------------|
| `Id`                 | int    | Variant ID to update (required if `ItemNumber` not provided) |
| `ItemNumber`         | string | Variant SKU (required if `Id` not provided)                  |
| `ProductId`          | int    | Parent product ID                                            |
| `Price`              | double | Selling price                                                |
| `BuyingPrice`        | double | Purchase / cost price                                        |
| `Stock`              | int    | Inventory count                                              |
| `Status`             | boolean| Variant visibility                                           |
| `Weight`             | double | Variant weight                                               |
| `Ean`                | string | EAN barcode                                                  |
| `ItemNumberSupplier` | string | Supplier item number                                         |
| `MinAmount`          | int    | Minimum order quantity                                       |
| `Discount`           | double | Discount amount                                              |
| `DiscountType`       | string | `'p'` for percent, `'a'` for amount                          |
| `DisableOnEmpty`     | boolean| Hide when out of stock                                       |
| `Description`        | string | Variant description                                          |
| `DescriptionLong`    | string | Long description                                             |
| `DeliveryTimeId`     | int    | Delivery time ID                                             |
| `Sorting`            | int    | Sort position                                                |
| `UnitId`             | int    | Unit of measure ID                                           |
| `PictureId`          | int    | *(Deprecated)* — use `PictureIds`                            |
| `PictureIds`         | int[]  | Picture IDs                                                  |
| `VariantTypeValues`  | int[]  | Variant attribute value IDs                                  |
| `StockLow`           | int    | Low stock warning threshold                                  |

---

## 7. Key API Operations

### Product Retrieval

| Operation                            | Parameters                       | Returns             | Description                                            |
|--------------------------------------|----------------------------------|---------------------|--------------------------------------------------------|
| `Product_GetAll`                     | *(none)*                         | `Product[]`         | Fetch all products in one call                         |
| `Product_GetByItemNumber`            | `ItemNumber` (string)            | `Product`           | Find a base product by its SKU                         |
| `Product_GetVariantsByItemNumber`    | `ItemNumber` (string)            | `ProductVariant[]`  | Find variant(s) by a variant-specific SKU              |
| `Product_GetAllWithLimit`            | `Start` (int), `Length` (int)    | `Product[]`         | Paginated fetch of all products                        |
| `Product_GetByBrand`                 | `BrandId` (int)                  | `Product[]`         | Fetch all products for a specific brand/producer       |

### Product Updates

| Operation                            | Parameters                       | Description                                                      |
|--------------------------------------|----------------------------------|------------------------------------------------------------------|
| `Product_Update`                     | `ProductData` (ProductUpdate)    | Update a base product — only set fields are changed              |
| `Product_UpdateVariant`              | `VariantData` (ProductVariantUpdate) | Update a variant — only set fields are changed               |
| `Product_Create`                     | `ProductData` (ProductUpdate)    | Create a new base product                                        |
| `Product_CreateVariant`              | `VariantData` (ProductVariantUpdate) | Create a new variant                                         |

### Session & Configuration

| Operation                            | Parameters                                 | Description                                          |
|--------------------------------------|--------------------------------------------|------------------------------------------------------|
| `Solution_Connect`                   | `Username` (string), `Password` (string)   | Authenticate the SOAP session                        |
| `Solution_SetLanguage`               | `LanguageISO` (string)                     | Set the active language for subsequent calls         |
| `Product_SetFields`                  | `Fields` (comma-separated string)          | Configure which fields appear in Product GET results |
| `Product_SetVariantFields`           | `Fields` (comma-separated string)          | Configure which fields appear in Variant GET results |

### User / Brand Retrieval

Brands (producers) are stored as **User** objects in the HostedShop
system.  The *Mærker* user group (typically ID 2) contains all
brand/producer users.

| Operation                            | Parameters                       | Returns        | Description                                           |
|--------------------------------------|----------------------------------|----------------|-------------------------------------------------------|
| `User_GetByGroup`                    | `UserGroupId` (int)              | `User[]`       | Fetch all users in a user group (e.g. brands)         |
| `User_GetById`                       | `UserId` (int)                   | `User`         | Fetch a single user by ID                             |
| `User_GetAll`                        | *(none)*                         | `User[]`       | Fetch all users                                       |
| `User_GetByName`                     | `UserName` (string)              | `User`         | Fetch a user by username                              |
| `User_GetGroupAll`                   | `withInterests` (bool)           | `UserGroup[]`  | Fetch all user groups                                 |
| `User_GetGroupById`                  | `UserGroupId` (int)              | `UserGroup`    | Fetch a user group by ID                              |

> **Important:** The parameter name for ``User_GetByGroup`` is
> ``UserGroupId`` — **not** ``GroupId``.

---

## 8. Field Name Reference

### Price Fields

The API uses consistent field names across all four types:

| Concept              | Field Name     | Type   | Present In                                                   |
|----------------------|----------------|--------|--------------------------------------------------------------|
| Selling price        | `Price`        | double | Product, ProductVariant, ProductUpdate, ProductVariantUpdate |
| Cost / purchase price| `BuyingPrice`  | double | Product, ProductVariant, ProductUpdate, ProductVariantUpdate |

> **Note:** The field name is `BuyingPrice` in all contexts (GET
> responses *and* update objects).  Some external references may use
> the term "CostPrice" as a conceptual synonym, but the **actual SOAP
> field name** recognised by the API is `BuyingPrice`.

### Minimal Price-Update Payloads

To update only prices without touching other product data, send a
minimal object with just the ID and price fields:

**Base product:**
```python
Product_Update(ProductData={"Id": 12345, "Price": 199.00, "BuyingPrice": 95.00})
```

**Variant:**
```python
Product_UpdateVariant(VariantData={"Id": 67890, "Price": 199.00, "BuyingPrice": 95.00})
```

### Identity Fields

| Context         | Field        | Description                                         |
|-----------------|--------------|-----------------------------------------------------|
| Base product    | `Id`         | Internal numeric product ID                         |
| Base product    | `ItemNumber`  | SKU — used for ERP syncing / lookup                |
| Variant         | `Id`         | **Variant** ID (different from parent product ID)   |
| Variant         | `ProductId`  | Parent product ID                                   |
| Variant         | `ItemNumber`  | Variant-specific SKU (e.g. `16022-000-S`)          |
| Brand/Producer  | `ProducerId` | Brand ID (pass to `Product_GetByBrand`)             |
| Brand/Producer  | `Producer`   | Complex `User` object — brand name in `.Company`    |

---

*Documentation sourced from the official HostedShop API reference at
<https://api.hostedshop.io/doc/Hosted%20Solution%20API/>.*
*Last verified: 2026-03-30.*
