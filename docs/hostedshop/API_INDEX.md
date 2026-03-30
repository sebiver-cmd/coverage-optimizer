# HostedShop API — Currency-Related Classes & Methods

> **Source of truth:** `data/hostedshop_docs/hostedshop_api_docs_full.md`

## Currency Conversion

**Not found in the provided documentation.** The HostedShop API does not expose any dedicated currency-conversion endpoint or method. The API provides CRUD operations for currency objects (create, read, update, delete) and a data representation of the currency used on an order, but no method that converts an amount from one currency to another.

---

## Classes

### Currency

| Field | Type | Description |
|---|---|---|
| `$Id` | int | The id of the Currency |
| `$Currency` | double | The value of the Currency |
| `$Iso` | string | The ISOcode of the Currency |
| `$Title` | string | The title of the Currency |
| `$Symbol` | string | The symbol of the currency |
| `$SymbolPlace` | string | Whether the currency symbol belongs on the 'left' or 'right' side of the number |
| `$Decimal` | string | The decimal character for the currency |
| `$DecimalCount` | string | The number of decimals after the decimalmark of the currency |
| `$Point` | string | The thousand seperator character for the currency |
| `$Round` | int | Indicates how Products in the Order are rounded for the Currency (0 no rounding, 1 round up to closest integer, 2 round down to closest integer, 3 round half up) |
| `$RoundOn` | int | Indicates on what decimal the price is rounded |

**URL heading:** `https://api.hostedshop.io/doc/Hosted Solution API/Currency.html`

---

### CurrencyCreate

Data object used when creating a new Currency. Same fields as `Currency` except `$Id` and `$RoundOn` are absent.

| Field | Type | Description |
|---|---|---|
| `$Currency` | double | The value of the Currency |
| `$Iso` | string | The ISOcode of the Currency |
| `$Title` | string | The title of the Currency |
| `$Symbol` | string | The symbol of the currency |
| `$SymbolPlace` | string | Whether the currency symbol belongs on the 'left' or 'right' side of the number |
| `$Decimal` | string | The decimal character for the currency |
| `$DecimalCount` | string | The number of decimals after the decimalmark of the currency |
| `$Point` | string | The thousand seperator character for the currency |
| `$Round` | int | Indicates how Products in the Order are rounded for the Currency (0 no rounding, 1 round up to closest integer, 2 round down to closest integer, 3 round half up) |

**URL heading:** `https://api.hostedshop.io/doc/Hosted Solution API/CurrencyCreate.html`

---

### CurrencyUpdate

Data object used when updating an existing Currency. Same fields as `Currency` except `$RoundOn` is absent.

| Field | Type | Description |
|---|---|---|
| `$Id` | int | The id of the Currency |
| `$Currency` | double | The value of the Currency |
| `$Iso` | string | The ISOcode of the Currency |
| `$Title` | string | The title of the Currency |
| `$Symbol` | string | The symbol of the currency |
| `$SymbolPlace` | string | Whether the currency symbol belongs on the 'left' or 'right' side of the number |
| `$Decimal` | string | The decimal character for the currency |
| `$DecimalCount` | string | The number of decimals after the decimalmark of the currency |
| `$Point` | string | The thousand seperator character for the currency |
| `$Round` | int | Indicates how Products in the Order are rounded for the Currency (0 no rounding, 1 round up to closest integer, 2 round down to closest integer, 3 round half up) |

**URL heading:** `https://api.hostedshop.io/doc/Hosted Solution API/CurrencyUpdate.html`

---

### OrderCurrency

Data object representing the currency attached to an order.

| Field | Type | Description |
|---|---|---|
| `$Id` | int | The id of the OrderCurrency |
| `$OrderId` | int | The id of the order of the OrderCurrency |
| `$Currency` | double | The value of the OrderCurrency |
| `$Iso` | string | The ISOcode of the OrderCurrency |
| `$Symbol` | string | The symbol of the currency |
| `$SymbolPlace` | string | Whether the currency symbol belongs on the 'left' or 'right' side of the number |
| `$Decimal` | string | The decimal character for the currency |
| `$Point` | string | The thousand seperator character for the currency |
| `$Round` | int | Indicates how Products in the Order are rounded for the Currency (0 no rounding, 1 round up to closest integer, 2 round down to closest integer, 3 round half up) |

**URL heading:** `https://api.hostedshop.io/doc/Hosted Solution API/OrderCurrency.html`

---

## SOAP Methods

All methods below are documented under **URL heading:** `https://api.hostedshop.io/doc/Hosted Solution API/_scripts---WebService.php.html`

### `Currency_GetAll`

Returns all currencies configured in the shop.

| | |
|---|---|
| **Parameters** | *(none)* |
| **Return type** | `Currency[]` — An array of Currency Objects |

---

### `Currency_GetByIso`

Returns a single currency by its ISO code.

| | |
|---|---|
| **Parameters** | `string $Iso` — The isocode of the wanted Currency |
| **Return type** | `Currency` — A Currency Object |

---

### `Currency_Create`

Creates a new currency.

| | |
|---|---|
| **Parameters** | `CurrencyCreate $CurrencyData` — The input in CurrencyCreate Object format |
| **Return type** | `int` — The id of the newly created Currency |

---

### `Currency_Update`

Updates an existing currency.

| | |
|---|---|
| **Parameters** | `CurrencyUpdate $CurrencyData` — The input in CurrencyUpdate Object format |
| **Return type** | `int` — The id of the Currency |

---

### `Currency_Delete`

Deletes a currency.

| | |
|---|---|
| **Parameters** | `int $CurrencyId` — The id of the Currency to delete |
| **Return type** | `boolean` |
