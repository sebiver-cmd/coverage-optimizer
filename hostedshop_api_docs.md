

---
## Source: https://api.hostedshop.io/doc/

xml version="1.0" encoding="iso-8859-1"?




Hosted Solution API








&lt;H2&gt;Frame Alert&lt;/H2&gt;
&lt;P&gt;This document is designed to be viewed using the frames feature.
If you see this message, you are using a non-frame-capable web client.&lt;/P&gt;

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Category.html

## Class Category

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for a Category

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

string
[$Description](#$Description "details")

string
[$DescriptionBottom](#$DescriptionBottom "details")

int
[$Id](#$Id "details")

String[]
[$LanguageAccess](#$LanguageAccess "details")

string
[$LanguageISO](#$LanguageISO "details")

int
[$ParentId](#$ParentId "details")

string
[$SeoCanonical](#$SeoCanonical "details")

string
[$SeoDescription](#$SeoDescription "details")

string
[$SeoKeywords](#$SeoKeywords "details")

string
[$SeoLink](#$SeoLink "details")

string
[$SeoTitle](#$SeoTitle "details")

int[]
[$ShowInMenu](#$ShowInMenu "details")

int
[$Sorting](#$Sorting "details")

boolean
[$Status](#$Status "details")

string
[$Title](#$Title "details")

int[]
[$UserGroupAccessIds](#$UserGroupAccessIds "details")

Variables

[Description](#sec-description) |
Vars

string
$Description

The description of the Category in the language indicated by the LanguageISO

string
$DescriptionBottom

The bottom description of the Category in the language indicated by the LanguageISO

int
$Id

The id of the Category

String[]
$LanguageAccess

Specifies on which languages and sites this entity is accessible. An array of LANGUAGE-ISO\_SITE-ID

string
$LanguageISO

The language ISO code of the Category for the active language (Solution\_SetLanguage)

int
$ParentId

The id of the parent Category of the Category

string
$SeoCanonical

The seo canonical of the Category in the language indicated by the LanguageISO

string
$SeoDescription

The Seo description of the Category in the language indicated by the LanguageISO

string
$SeoKeywords

The seo keywords of the Category in the language indicated by the LanguageISO

string
$SeoLink

The Seo link of the Category in the language indicated by the LanguageISO

string
$SeoTitle

The seo title of the Category in the language indicated by the LanguageISO

int[]
$ShowInMenu

The Ids of the UserGroups that can view this category in the frontend menu (null for all, array[] for none, array[0,1,2,3] for specific). The UserGroup with id 0 represents "visible for guests".

int
$Sorting

The position of the Category amongst its siblings

boolean
$Status

Whether or not the Category is visible in the shop

string
$Title

The title of the Category in the language indicated by the LanguageISO

int[]
$UserGroupAccessIds

The Ids of the UserGroups that can access this product of the Product (empty for all). The UserGroup with id 0 represents "visible for guests"

Documentation generated on Thu, 26 Jul 2018 13:22:44 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Country.html

# Not Found

The requested URL /doc/Hosted Solution API/Country.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Currency.html

## Class Currency

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for a Currency

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

double
[$Currency](#$Currency "details")

string
[$Decimal](#$Decimal "details")

string
[$DecimalCount](#$DecimalCount "details")

int
[$Id](#$Id "details")

string
[$Iso](#$Iso "details")

string
[$Point](#$Point "details")

int
[$Round](#$Round "details")

int
[$RoundOn](#$RoundOn "details")

string
[$Symbol](#$Symbol "details")

string
[$SymbolPlace](#$SymbolPlace "details")

string
[$Title](#$Title "details")

Variables

[Description](#sec-description) |
Vars

double
$Currency

The value of the Currency

string
$Decimal

The decimal character for the currency

string
$DecimalCount

The number of decimals after the decimalmark of the currency

int
$Id

The id of the Currency

string
$Iso

The ISOcode of the Currency

string
$Point

The thousand seperator character for the currency

int
$Round

Indicates how Products in the Order are rounded for the Currency (0 no rounding, 1 round up to closest integer, 2 round down to closest integer, 3 round half up)

int
$RoundOn

Indicates on what decimal the price is rounded

string
$Symbol

The symbol of the currency

string
$SymbolPlace

Whether the currency symbol belongs on the 'left' or 'right' side of the number

string
$Title

The title of the Currency

Documentation generated on Thu, 26 Jul 2018 13:22:45 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Delivery.html

## Class Delivery

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for an Order Delivery

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

bool
[$DeliveryEstimate](#$DeliveryEstimate "details")

bool
[$FixedDelivery](#$FixedDelivery "details")

bool
[$FreeDeliveryActive](#$FreeDeliveryActive "details")

double
[$FreeDeliveryPrice](#$FreeDeliveryPrice "details")

int
[$Id](#$Id "details")

string
[$LanguageISO](#$LanguageISO "details")

bool
[$MultipleAddresses](#$MultipleAddresses "details")

bool
[$OverLimitFeeActive](#$OverLimitFeeActive "details")

double
[$OverLimitFixedFee](#$OverLimitFixedFee "details")

double
[$OverLimitPercentageFee](#$OverLimitPercentageFee "details")

double
[$Price](#$Price "details")

bool
[$Primary](#$Primary "details")

bool
[$RegionMode](#$RegionMode "details")

string
[$ServiceType](#$ServiceType "details")

int
[$Sorting](#$Sorting "details")

string
[$Text](#$Text "details")

string
[$Title](#$Title "details")

string
[$Type](#$Type "details")

int[]
[$UserGroups](#$UserGroups "details")

boolean
[$Vat](#$Vat "details")

int
[$ZipFrom](#$ZipFrom "details")

int[]
[$ZipGroups](#$ZipGroups "details")

int
[$ZipTo](#$ZipTo "details")

Variables

[Description](#sec-description) |
Vars

bool
$DeliveryEstimate

Wether or not the Delivery should be included in a Delivery Estimate

bool
$FixedDelivery

Wether or not the Delivery is available for Fixed Delivery

bool
$FreeDeliveryActive

Indicates whether or not the Delivery has free delivery active

double
$FreeDeliveryPrice

The price limit to activate free delivery

int
$Id

The id of the Delivery

string
$LanguageISO

The language Language ISO code of the Delivery for the active language (Solution\_SetLanguage)

bool
$MultipleAddresses

Wether or not the Delivery is available for Multiple Address delivery

bool
$OverLimitFeeActive

Indicates whether or not the Delivery has over limit pricing active

double
$OverLimitFixedFee

The fixed fee of the over limit pricing

double
$OverLimitPercentageFee

The percentage (of the order price) of the over limit pricing

double
$Price

The price of this Delivery method (calculated from the Weight of the order supplied in Delivery\_GetByRegion)

bool
$Primary

Whether or not the Delivery is the primary delivery method

bool
$RegionMode

Indicates whether the region mode of the delivery (all, range, list)

string
$ServiceType

The the service type of the Delivery

int
$Sorting

The position of the Delivery amongst its siblings

string
$Text

The text of the Delivery

string
$Title

The title of the Delivery

string
$Type

The type of the Delivery

int[]
$UserGroups

The ids UserGroups for which this Delivery is Available

boolean
$Vat

Indicates whether or not the Delivery has vat

int
$ZipFrom

The lowest Zip of the Delivery (for RegionMode range)

int[]
$ZipGroups

The Zips of the Delivery (for RegionMode list)

int
$ZipTo

The highest Zip of the Delivery (for RegionMode range)

Documentation generated on Thu, 26 Jul 2018 13:22:46 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Discount.html

## Class Discount

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for a Discount

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

int
[$AmountSpent](#$AmountSpent "details")

string
[$Code](#$Code "details")

string
[$DateCreated](#$DateCreated "details")

string
[$DateExpire](#$DateExpire "details")

int
[$Id](#$Id "details")

int
[$Limit](#$Limit "details")

int[]
[$ProductIds](#$ProductIds "details")

DiscountCustomerAssociation
[DiscountCustomerAssociation](#$DiscountCustomerAssociation "details")

DiscountCustomerGroupAssociation
[DiscountCustomerGroupAssociation](#$DiscountCustomerGroupAssociation "details")

string
[$Title](#$Title "details")

string
[$Type](#$Type "details")

int
[$UseCount](#$UseCount "details")

double
[$Value](#$Value "details")

int
[$Vat](#$Vat "details")

dateTime
[$StartDate](#$StartDate "details")

bool
[$IsActive](#$IsActive "details")

bool
[$IsRestrictedToNewCustomer](#$IsRestrictedToNewCustomer "details")

double
[$MinimumCartValue](#$MinimumCartValue "details")

[DiscountedProductCategories](../Hosted Solution API/DiscountedProductCategories.html)
[$DiscountedProductCategories](#$DiscountedProductCategories "details")

bool
[$IsSingleUsePerCustomer](#$IsSingleUsePerCustomer "details")

Variables

[Description](#sec-description) |
Vars

int
$AmountSpent

The spent amount of the Discount

string
$Code

The code of the Discount

string
$DateCreated

The date the Discount was created

string
$DateExpire

The expiry date of the Discount (empty for none)

int
$Id

The id of the Discount

int
$Limit

The usage limit of the Discount

int[]
$ProductIds

The ids of the products related to this Discount

DiscountCustomerAssociation
$DiscountCustomerAssociation

The customer associations related to this Discount

DiscountCustomerGroupAssociation
$DiscountCustomerGroupAssociation

The customer group associations related to this Discount

string
$Title

The title of the Discount

string
$Type

The type of the Discount ('p' for percentage, 'f' for fixed amount)

int
$UseCount

The use count of the Discount

double
$Value

The value of the Discount (either percentage of fixed amount according to the Type)

int
$Vat

Wether or not the Discount has VAT

dateTime
$StartDate

The start date of the discount

bool
$IsActive

If the Discount is active

bool
$IsRestrictedToNewCustomer

If the Discount is restricted to new customers

double
$MinimumCartValue

Minimum cart discount to to make discount applicable

[DiscountedProductCategories](../Hosted Solution API/DiscountedProductCategories.html)
$DiscountedProductCategories

The product category restrictions of this Discount

bool
$IsSingleUsePerCustomer

If the Discount is restricted to a single use per customer

Documentation generated on Thu, 26 Jul 2018 13:22:46 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Invoice.html

# Not Found

The requested URL /doc/Hosted Solution API/Invoice.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Language.html

# Not Found

The requested URL /doc/Hosted Solution API/Language.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Manufacturer.html

# Not Found

The requested URL /doc/Hosted Solution API/Manufacturer.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Media.html

# Not Found

The requested URL /doc/Hosted Solution API/Media.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Order.html

## Class Order

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for an Order

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

[OrderCurrency](../Hosted Solution API/OrderCurrency.html)
[$Currency](#$Currency "details")

int
[$CurrencyId](#$CurrencyId "details")

[OrderCustomer](../Hosted Solution API/OrderCustomer.html)
[$Customer](#$Customer "details")

string
[$CustomerComment](#$CustomerComment "details")

int
[$CustomerId](#$CustomerId "details")

string
[$DateDelivered](#$DateDelivered "details")

string
[$DateDue](#$DateDue "details")

string
[$DateSent](#$DateSent "details")

string
[$DateUpdated](#$DateUpdated "details")

[OrderDelivery](../Hosted Solution API/OrderDelivery.html)
[$Delivery](#$Delivery "details")

string
[$DeliveryComment](#$DeliveryComment "details")

int
[$DeliveryId](#$DeliveryId "details")

string
[$DeliveryTime](#$DeliveryTime "details")

OrderDiscountCode[]
[$DiscountCodes](#$DiscountCodes "details")

int
[$Id](#$Id "details")

int
[$InvoiceNumber](#$InvoiceNumber "details")

string
[$LanguageISO](#$LanguageISO "details")

string
[$OrderComment](#$OrderComment "details")

string
[$OrderCommentExternal](#$OrderCommentExternal "details")

OrderLine[]
[$OrderLines](#$OrderLines "details")

string
[$Origin](#$Origin "details")

[OrderPacking](../Hosted Solution API/OrderPacking.html)
[$Packing](#$Packing "details")

int
[$PackingId](#$PackingId "details")

[OrderPayment](../Hosted Solution API/OrderPayment.html)
[$Payment](#$Payment "details")

int
[$PaymentId](#$PaymentId "details")

string
[$ReferenceNumber](#$ReferenceNumber "details")

string
[$Site](#$Site "details")

string
[$Status](#$Status "details")

double
[$Total](#$Total "details")

string
[$TrackingCode](#$TrackingCode "details")

OrderTransaction[]
[$Transactions](#$Transactions "details")

User
[$User](#$User "details")

int
[$UserId](#$UserId "details")

double
[$Vat](#$Vat "details")

string
[$ReferralCode](#$ReferralCode "details")

Variables

[Description](#sec-description) |
Vars

[OrderCurrency](../Hosted Solution API/OrderCurrency.html)
$Currency

The OrderCurrency of the Order

int
$CurrencyId

The id of the Currency of the Order

[OrderCustomer](../Hosted Solution API/OrderCustomer.html)
$Customer

The Customer of the Order

string
$CustomerComment

The customer comment of the Order

int
$CustomerId

The id of the Customer of the Order

string
$DateDelivered

The creation datetime of the Order

string
$DateDue

The due datetime of the Order

string
$DateSent

The datetime when the Order was marked as delivered

string
$DateUpdated

The datetime of the last update performed on the Order

[OrderDelivery](../Hosted Solution API/OrderDelivery.html)
$Delivery

The Delivery of the Order

string
$DeliveryComment

The delivery comment of the Order

int
$DeliveryId

The id of the Delivery of the Order

string
$DeliveryTime

The delivery time of the Order

OrderDiscountCode[]
$DiscountCodes

The Discounts of the Order

int
$Id

The id of the Order

int
$InvoiceNumber

The invoice number of the Order

string
$LanguageISO

The ISO code of the language of the Order.

string
$OrderComment

The internal comment of the Order

string
$OrderCommentExternal

The external comment of the Order

OrderLine[]
$OrderLines

The OrderLines of the Order

string
$Origin

The origin of the Order (default Webshop)

[OrderPacking](../Hosted Solution API/OrderPacking.html)
$Packing

The Packing of the Order

int
$PackingId

The id of the Packing of the Order

[OrderPayment](../Hosted Solution API/OrderPayment.html)
$Payment

The Payment of the Order

int
$PaymentId

The id of the Payment of the Order

string
$ReferenceNumber

The Reference number of the Order

string
$Site

The Site of the Order

string
$Status

The Status of the Order

double
$Total

The total price of the Order without vat, shipping, and transaction price. (deprecated) This field is deprecated and might not represent the correct order total. For a correct calculation sum the value of order lines, shipping and transaction price

string
$TrackingCode

The trackingcode of the Order

OrderTransaction[]
$Transactions

The transactions of the Order

User
$User

The user of the Order

int
$UserId

The id of the User of the Order

double
$Vat

The Vat percentage of the Order

string
$ReferralCode

The referral code of the Order

Documentation generated on Thu, 26 Jul 2018 13:22:46 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/OrderLine.html

## Class OrderLine

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for an Orderline

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

string
[$AdditionalTitle](#$AdditionalTitle "details")

int
[$Amount](#$Amount "details")

double
[$BuyPrice](#$BuyPrice "details")

int
[$DeliveryId](#$DeliveryId "details")

double
[$Discount](#$Discount "details")

double
[$DiscountRounded](#$DiscountRounded "details")

string
[$ExtendedDataExternal](#$ExtendedDataExternal "details")

string
[$ExtendedDataInternal](#$ExtendedDataInternal "details")

int
[$FileDownloadId](#$FileDownloadId "details")

int
[$Id](#$Id "details")

string
[$ItemNumber](#$ItemNumber "details")

string
[$ItemNumberSupplier](#$ItemNumberSupplier "details")

OrderLineAddress[]
[$LineAddresses](#$LineAddresses "details")

bool
[$OfflineProduct](#$OfflineProduct "details")

int
[$OrderId](#$OrderId "details")

int
[$PacketId](#$PacketId "details")

OrderLine[]
[$PacketLines](#$PacketLines "details")

string
[$PacketTitle](#$PacketTitle "details")

double
[$Price](#$Price "details")

double
[$PriceRounded](#$PriceRounded "details")

int
[$ProductId](#$ProductId "details")

string
[$ProductTitle](#$ProductTitle "details")

string
[$ServiceType](#$ServiceType "details")

string
[$Status](#$Status "details")

int
[$StockLocationId](#$StockLocationId "details")

string
[$StockStatus](#$StockStatus "details")

string
[$TrackingCode](#$TrackingCode "details")

string
[$Unit](#$Unit "details")

int
[$VariantId](#$VariantId "details")

string
[$VariantTitle](#$VariantTitle "details")

double
[$Vat](#$Vat "details")

double
[$VatRate](#$VatRate "details")

double
[$Weight](#$Weight "details")

Variables

[Description](#sec-description) |
Vars

string
$AdditionalTitle

Comma seperated list of the selected ProductAdditionalTypes when the Order was made

int
$Amount

The amount of Products of the OrderLine

double
$BuyPrice

The buying price of the Product of the OrderLine when the Order was made

int
$DeliveryId

The delivery id of the OrderLine

double
$Discount

The discount given on the Product of the OrderLine when the Order was made

double
$DiscountRounded

The rounded discount given on the Product of the OrderLine when the Order was made

string
$ExtendedDataExternal

Extended product data (displayed externally) of the OrderLine

string
$ExtendedDataInternal

Extended product data (displayed internally) of the OrderLine

int
$FileDownloadId

The id of the OrderFileDownload of the OrderLine

int
$Id

The id of the OrderLine

string
$ItemNumber

The item number of the Product of the OrderLine when the Order was made

string
$ItemNumberSupplier

The supplier item number of the Product

OrderLineAddress[]
$LineAddresses

The the OrderLineAddresses of this OrderLine

bool
$OfflineProduct

Wether or not this orderline represents an offline product

int
$OrderId

The id of the order to which this line belongs

int
$PacketId

The id of the ProductVariants represented by the OrderLine when the Order was made

OrderLine[]
$PacketLines

Contains the Orderlines in the Package represented by the OrderLine, if any

string
$PacketTitle

The packet title of the OrderLine when the Order was made

double
$Price

The price of the Product represented when the Order was made

double
$PriceRounded

The rounded price of the Product represented when the Order was made

int
$ProductId

The id of the product of the OrderLine

string
$ProductTitle

The title of the Product represented by the OrderLine when the Order was made

string
$ServiceType

The service type of the OrderLine

string
$Status

The Status of the OrderLine

int
$StockLocationId

The stock location id of the OrderLine

string
$StockStatus

The tracking code of the OrderLine

string
$TrackingCode

The stock status description of the Product of the OrderLine when the Order was made

string
$Unit

The unit text of the OrderLine

int
$VariantId

The id of the OrderVariant of the OrderLine

string
$VariantTitle

The title of the ProductVariants represented by the OrderLine when the Order was made

double
$Vat

The VAT given on the Product of the OrderLine when the Order was made

double
$VatRate

The vatrate of the OrderLine

double
$Weight

The weight of the Product of the OrderLine when the Order was made

Documentation generated on Thu, 26 Jul 2018 13:22:47 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Payment.html

# Not Found

The requested URL /doc/Hosted Solution API/Payment.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Product.html

## Class Product

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for a Product

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

ProductAdditionalType[]
[$Additionals](#$Additionals "details")

AutoStock
[$AutoStock](#$AutoStock "details")

double
[$BuyingPrice](#$BuyingPrice "details")

boolean
[$CallForPrice](#$CallForPrice "details")

Category
[$Category](#$Category "details")

int
[$CategoryId](#$CategoryId "details")

ProductCategorySorting[]
[$CategorySortings](#$CategorySortings "details")

ProductCustomData[]
[$CustomData](#$CustomData "details")

string
[$DateCreated](#$DateCreated "details")

string
[$DateUpdated](#$DateUpdated "details")

Delivery
[$Delivery](#$Delivery "details")

int
[$DeliveryId](#$DeliveryId "details")

[ProductDeliveryTime](../Hosted Solution API/ProductDeliveryTime.html)
[$DeliveryTime](#$DeliveryTime "details")

int
[$DeliveryTimeId](#$DeliveryTimeId "details")

string
[$Description](#$Description "details")

string
[$DescriptionLong](#$DescriptionLong "details")

string
[$DescriptionShort](#$DescriptionShort "details")

boolean
[$DisableOnEmpty](#$DisableOnEmpty "details")

double
[$Discount](#$Discount "details")

DiscountGroup
[$DiscountGroup](#$DiscountGroup "details")

int
[$DiscountGroupId](#$DiscountGroupId "details")

ProductDiscount[]
[$Discounts](#$Discounts "details")

string
[$DiscountType](#$DiscountType "details")

string
[$Ean](#$Ean "details")

ProductExtraBuyRelation[]
[$ExtraBuyRelations](#$ExtraBuyRelations "details")

boolean
[$FocusCart](#$FocusCart "details")

boolean
[$FocusFrontpage](#$FocusFrontpage "details")

double
[$GuidelinePrice](#$GuidelinePrice "details")

int
[$Id](#$Id "details")

string
[$ItemNumber](#$ItemNumber "details")

string
[$ItemNumberSupplier](#$ItemNumberSupplier "details")

String[]
[$LanguageAccess](#$LanguageAccess "details")

string
[$LanguageISO](#$LanguageISO "details")

int
[$MinAmount](#$MinAmount "details")

boolean
[$Online](#$Online "details")

OutOfStockBuy
[$OutOfStockBuy](#$OutOfStockBuy "details")

PacketProductLine[]
[$PacketProducts](#$PacketProducts "details")

ProductPicture[]
[$Pictures](#$Pictures "details")

double
[$Price](#$Price "details")

[User](../Hosted Solution API/User.html)
[$Producer](#$Producer "details")

int
[$ProducerId](#$ProducerId "details")

string
[$ProductUrl](#$ProductUrl "details")

int[]
[$RelatedProductIds](#$RelatedProductIds "details")

string
[$RelationCode](#$RelationCode "details")

Category[]
[$SecondaryCategories](#$SecondaryCategories "details")

int[]
[$SecondaryCategoryIds](#$SecondaryCategoryIds "details")

string
[$SeoCanonical](#$SeoCanonical "details")

string
[$SeoDescription](#$SeoDescription "details")

string
[$SeoKeywords](#$SeoKeywords "details")

string
[$SeoLink](#$SeoLink "details")

string
[$SeoTitle](#$SeoTitle "details")

int
[$Sorting](#$Sorting "details")

boolean
[$Status](#$Status "details")

int
[$Stock](#$Stock "details")

VatGroup
[$StockLocationId](#$StockLocationId "details")

int
[$StockLow](#$StockLow "details")

ProductStockLocation[]
[$StockLocations](#$StockLocations "details")

ProductTag[]
[$Tags](#$Tags "details")

string
[$Title](#$Title "details")

mixed
[$Type](#$Type "details")

[ProductUnit](../Hosted Solution API/ProductUnit.html)
[$Unit](#$Unit "details")

int
[$UnitId](#$UnitId "details")

string
[$Url](#$Url "details")

User[]
[$UserAccess](#$UserAccess "details")

int[]
[$UserAccessIds](#$UserAccessIds "details")

UserGroup[]
[$UserGroupAccess](#$UserGroupAccess "details")

int[]
[$UserGroupAccessIds](#$UserGroupAccessIds "details")

ProductVariant[]
[$Variants](#$Variants "details")

string
[$VariantTypes](#$VariantTypes "details")

VatGroup
[$VatGroup](#$VatGroup "details")

int
[$VatGroupId](#$VatGroupId "details")

double
[$Weight](#$Weight "details")

string
[$TypeLabel](#$TypeLabel "details")

Variables

[Description](#sec-description) |
Vars

ProductAdditionalType[]
$Additionals

The ProductAdditionalTypes of the Product

AutoStock
$AutoStock

Whether or not stock should be automatically controlled for the product. 1 for true, 0 for false, blank to inherit value from site

double
$BuyingPrice

The internal purchase price of the Product

boolean
$CallForPrice

Indicates whether or not 'Call for Price' information is available for this Product

Category
$Category

The primary Category of the Product

int
$CategoryId

The id of the primary Category of the Product

ProductCategorySorting[]
$CategorySortings

Product/Category sorting

[CategoryId,Sorting] combinations

ProductCustomData[]
$CustomData

The ProductCustomDatas of the Product

string
$DateCreated

The datetime the Product was created

string
$DateUpdated

The datetime of the lastest update of the Product

Delivery
$Delivery

the Delivery of the Product

int
$DeliveryId

the id of the Delivery of the Product

[ProductDeliveryTime](../Hosted Solution API/ProductDeliveryTime.html)
$DeliveryTime

the DeliveryTime of this product, (returns null for the "standard" and "standard\_lager" types)

int
$DeliveryTimeId

the id of the DeliveryTime, (0 to display amount of product in stock. -1 for no information at all)

string
$Description

The description of the Product in the language indicated by the LanguageISO

string
$DescriptionLong

The long description of the Product in the language indicated by the LanguageISO

string
$DescriptionShort

The short description of the Product in the language indicated by the LanguageISO

boolean
$DisableOnEmpty

Indicates whether or not the Product should be hidden in the shop when it is not in stock

double
$Discount

The discount on the Product

DiscountGroup
$DiscountGroup

The DiscountGroup of this Product

int
$DiscountGroupId

The id of the DiscountGroup of this Product

ProductDiscount[]
$Discounts

The custom discounts on this product

string
$DiscountType

The type of discount given in Discount, either 'p' for percent or 'a' for a amount

string
$Ean

The Ean number of the Product

ProductExtraBuyRelation[]
$ExtraBuyRelations

Returns ProductExtraBuyRelations of the Product

boolean
$FocusCart

Whether or not the Product should be displayed beneath the cart for extra focus

boolean
$FocusFrontpage

Whether or not the Product should be displayed beneath the frontpage content for extra focus

double
$GuidelinePrice

The guideline price of the product

int
$Id

The id of the Product

string
$ItemNumber

The item number of the Product

string
$ItemNumberSupplier

The supplier item number of the Product

String[]
$LanguageAccess

Specifies on which languages and sites this entity is accessible. An array of LANGUAGE-ISO\_SITE-ID. Empty indicates that the Entity has no limit on access.

string
$LanguageISO

The language Language ISO code of the Product for the active language (Solution\_SetLanguage)

int
$MinAmount

The minimum amount which can be ordered this product

boolean
$Online

Indicates whether or not the Product is an online product (filesale)

OutOfStockBuy
$OutOfStockBuy

The purchase status of the product when out of stock. 1 for true, 0 for false, blank to inherit value from site

PacketProductLine[]
$PacketProducts

The PacketproductLines of the Product

ProductPicture[]
$Pictures

The ProductPictures of the Product

double
$Price

The sellingprice of the Product

[User](../Hosted Solution API/User.html)
$Producer

The producer (User) of the Product

int
$ProducerId

The id of the producer (User) of the Product

string
$ProductUrl

The url for the product

int[]
$RelatedProductIds

The ids of the related Products of this Product

string
$RelationCode

The relationcode of the Product

Category[]
$SecondaryCategories

The secondary Categories of the Product

int[]
$SecondaryCategoryIds

The ids of the secondary Categories of the Product

string
$SeoCanonical

The seo canonical of the Product in the language indicated by the LanguageISO

string
$SeoDescription

The Seo description of the Product in the language indicated by the LanguageISO

string
$SeoKeywords

The seo keywords of the Product in the language indicated by the LanguageISO

string
$SeoLink

The Seo link of the Product in the language indicated by the LanguageISO

string
$SeoTitle

The seo title of the Product in the language indicated by the LanguageISO

int
$Sorting

The position of the Product amongst its siblings

boolean
$Status

Indicates wether or not this Product is visible in the shop

int
$Stock

Indicates the amount of this product in stock

VatGroup
$StockLocationId

The Stock Location id of the Product 0 for default

int
$StockLow

Indicates the amount of the low stock warning for this product

ProductStockLocation[]
$StockLocations

The ProductStockLocations of the Product

ProductTag[]
$Tags

The ProductTags of the Product

string
$Title

The title of the Product in the language indicated by the LanguageISO

mixed
$Type

The type of the Product (normal, giftcard, packet or discontinued)

[ProductUnit](../Hosted Solution API/ProductUnit.html)
$Unit

The ProductUnit of the Product

int
$UnitId

The id of the ProductUnit of the Product

string
$Url

External url describing the Product

User[]
$UserAccess

The Users that can access this product of the Product (empty for all)

int[]
$UserAccessIds

The ids of the Users that can access this product of the Product (empty for all)

UserGroup[]
$UserGroupAccess

The UserGroups that can access this product of the Product (empty for all). The UserGroup with id 0 represents "visible for guests"

int[]
$UserGroupAccessIds

The Ids of the UserGroups that can access this product of the Product (empty for all). The UserGroup with id 0 represents "visible for guests"

ProductVariant[]
$Variants

The ProductVariants of the Product

string
$VariantTypes

The title of the VariantTypes of the product (the name(s) of the VarientTypes concatted with //)

VatGroup
$VatGroup

The VatGroup of the Product (null if none)

int
$VatGroupId

The id of the VatGroup of the Product

double
$Weight

The weight of the Product

string
$TypeLabel

The type label of the Product. Possible values are: 'normal', 'file-sale', 'gift-card-with-code', 'gift-card-without-code', 'call-for-price', 'discontinued'.

Documentation generated on Thu, 26 Jul 2018 13:22:48 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductCategory.html

# Not Found

The requested URL /doc/Hosted Solution API/ProductCategory.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductRelation.html

# Not Found

The requested URL /doc/Hosted Solution API/ProductRelation.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/ProductVariant.html

## Class ProductVariant

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject Product Variant

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

double
[$BuyingPrice](#$BuyingPrice "details")

[ProductDeliveryTime](../Hosted Solution API/ProductDeliveryTime.html)
[$DeliveryTime](#$DeliveryTime "details")

int
[$DeliveryTimeId](#$DeliveryTimeId "details")

string
[$Description](#$Description "details")

string
[$DescriptionLong](#$DescriptionLong "details")

boolean
[$DisableOnEmpty](#$DisableOnEmpty "details")

double
[$Discount](#$Discount "details")

string
[$DiscountType](#$DiscountType "details")

string
[$Ean](#$Ean "details")

int
[$Id](#$Id "details")

string
[$ItemNumber](#$ItemNumber "details")

string
[$ItemNumberSupplier](#$ItemNumberSupplier "details")

int
[$MinAmount](#$MinAmount "details")

int
[$PictureId](#$PictureId "details")

int[]
[$PictureIds](#$PictureIds "details")

double
[$Price](#$Price "details")

int
[$ProductId](#$ProductId "details")

int
[$Sorting](#$Sorting "details")

boolean
[$Status](#$Status "details")

int
[$Stock](#$Stock "details")

ProductVariantStockLocation[]
[$StockLocations](#$StockLocations "details")

int
[$StockLow](#$StockLow "details")

string
[$Title](#$Title "details")

[ProductUnit](../Hosted Solution API/ProductUnit.html)
[$Unit](#$Unit "details")

int[]
[$VariantTypeValues](#$VariantTypeValues "details")

double
[$Weight](#$Weight "details")

Variables

[Description](#sec-description) |
Vars

double
$BuyingPrice

The internal purchase price of the ProductVariant

[ProductDeliveryTime](../Hosted Solution API/ProductDeliveryTime.html)
$DeliveryTime

the DeliveryTime of this ProductVariant, (returns null for the "standard" and "standard\_lager" types)

int
$DeliveryTimeId

the id of the DeliveryTime, (0 to display amount of product in stock. -1 for no information at all)

string
$Description

The description of the Variant in the language indicated by the LanguageISO

string
$DescriptionLong

The long description of the Variant in the language indicated by the LanguageISO

boolean
$DisableOnEmpty

Indicates whether or not the ProductVariant should be hidden in the shop when it is not in stock

double
$Discount

The discount on the ProductVariant

string
$DiscountType

The type of discount given in Discount, either 'p' for percent or 'a' for a amount

string
$Ean

The Ean number of the Variant

int
$Id

The id of the ProductVariant

string
$ItemNumber

The item number of the ProductVariant

string
$ItemNumberSupplier

The supplier item number of the Product

int
$MinAmount

The minimum amount which can be ordered this Variant

int
$PictureId

The id of the Picture of the ProductVariant. NOTE: This field is no longer in use. See PictureIds.

int[]
$PictureIds

The ids of the Pictures of the ProductVariant

double
$Price

The price of this ProductVariant

int
$ProductId

The id of the Product of the ProductVariant

int
$Sorting

The position of the ProductVariant amongst its siblings

boolean
$Status

Indicates wether or not this ProductVariant is visible in the shop

int
$Stock

Indicates the amount of this variant in stock

int
$StockLow

Indicates the amount of the low stock warning for this variant

ProductVariantStockLocation[]
$StockLocations

The ProductVariantStockLocations of the ProductVariant

string
$Title

The Title of the Variant (the name(s) of the VarientTypeValues concatted with //)

[ProductUnit](../Hosted Solution API/ProductUnit.html)
$Unit

The ProductUnit of the Variant

int[]
$VariantTypeValues

The IDs of the VariantTypeValues associated with the Variant

double
$Weight

The weight of the ProductVariant

Documentation generated on Thu, 26 Jul 2018 13:22:50 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Setting.html

# Not Found

The requested URL /doc/Hosted Solution API/Setting.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Shop.html

# Not Found

The requested URL /doc/Hosted Solution API/Shop.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/Stock.html

# Not Found

The requested URL /doc/Hosted Solution API/Stock.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/StockLocation.html

# Not Found

The requested URL /doc/Hosted Solution API/StockLocation.html was not found on this server.

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/User.html

## Class User

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for a User

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

string
[$Address](#$Address "details")

string
[$Address2](#$Address2 "details")

boolean
[$Approved](#$Approved "details")

string
[$BirthDate](#$BirthDate "details")

string
[$City](#$City "details")

string
[$Company](#$Company "details")

boolean
[$Consent](#$Consent "details")

datetime
[$ConsentDate](#$ConsentDate "details")

string
[$Country](#$Country "details")

int
[$CountryCode](#$CountryCode "details")

int
[$Currency](#$Currency "details")

CustomData[]
[$CustomData](#$CustomData "details")

string
[$Cvr](#$Cvr "details")

string
[$DateCreated](#$DateCreated "details")

string
[$DateUpdated](#$DateUpdated "details")

string
[$Description](#$Description "details")

int
[$DiscountGroupId](#$DiscountGroupId "details")

string
[$Ean](#$Ean "details")

string
[$Email](#$Email "details")

string
[$Fax](#$Fax "details")

string
[$Firstname](#$Firstname "details")

int
[$Id](#$Id "details")

int[]
[$InterestFields](#$InterestFields "details")

String[]
[$LanguageAccess](#$LanguageAccess "details")

string
[$LanguageISO](#$LanguageISO "details")

string
[$Lastname](#$Lastname "details")

string
[$Mobile](#$Mobile "details")

boolean
[$Newsletter](#$Newsletter "details")

string
[$Number](#$Number "details")

string
[$Password](#$Password "details")

string
[$Phone](#$Phone "details")

string
[$Referer](#$Referer "details")

int
[$Sex](#$Sex "details")

string
[$ShippingAddress](#$ShippingAddress "details")

string
[$ShippingAddress2](#$ShippingAddress2 "details")

string
[$ShippingCity](#$ShippingCity "details")

string
[$ShippingCompany](#$ShippingCompany "details")

string
[$ShippingCountry](#$ShippingCountry "details")

string
[$ShippingCountryCode](#$ShippingCountryCode "details")

string
[$ShippingCvr](#$ShippingCvr "details")

string
[$ShippingEan](#$ShippingEan "details")

string
[$ShippingEmail](#$ShippingEmail "details")

string
[$ShippingFirstname](#$ShippingFirstname "details")

string
[$ShippingLastname](#$ShippingLastname "details")

string
[$ShippingMobile](#$ShippingMobile "details")

string
[$ShippingPhone](#$ShippingPhone "details")

string
[$ShippingReferenceNumber](#$ShippingReferenceNumber "details")

string
[$ShippingState](#$ShippingState "details")

string
[$ShippingType](#$ShippingType "details")

string
[$ShippingZip](#$ShippingZip "details")

int
[$Site](#$Site "details")

string
[$Type](#$Type "details")

string
[$Url](#$Url "details")

int
[$UserGroupId](#$UserGroupId "details")

string
[$Username](#$Username "details")

string
[$Zip](#$Zip "details")

Variables

[Description](#sec-description) |
Vars

string
$Address

The address of the User

string
$Address2

The address 2 of the User

boolean
$Approved

Wether or not the user has been approved by an administrator

string
$BirthDate

The date of birth of the User

string
$City

The city of the User

string
$Company

The name of the company of this User

boolean
$Consent

The consent status of the user

datetime
$ConsentDate

The date that the user confirmed consent

string
$Country

The country of the User

int
$CountryCode

The phone initials of the Users country

int
$Currency

The currency iso of the User

CustomData[]
$CustomData

The Customdatas of this User

string
$Cvr

The Cvr number of the User

string
$DateCreated

The datetime the User was created

string
$DateUpdated

The datetime the User was last updated

string
$Description

The description of the User

int
$DiscountGroupId

The id of the DiscountGroup of the User

string
$Ean

The Ean number of the User

string
$Email

The email of the User

string
$Fax

The fax number of the User

string
$Firstname

The firstname of the user

int
$Id

The id of the User

int[]
$InterestFields

The ids InterestGroups of this User

String[]
$LanguageAccess

Specifies on which languages and sites this entity is accessible. An array of LANGUAGE-ISO\_SITE-ID

string
$LanguageISO

The selected frontend Language ISO of the User

string
$Lastname

The lastname of the user

string
$Mobile

The mobile number of the User

boolean
$Newsletter

Whether or not the User should receive Newsletters

string
$Number

The number of the User

string
$Password

The password of the User. This property will always return an empty result as to not expose sensitive data in regards to data security.

string
$Phone

The phone number of the User

string
$Referer

String describing from where the user was created

int
$Sex

Wether the User is male of female (1 for male, 2 for female, 0 for none)

string
$ShippingAddress

The shipping address

string
$ShippingAddress2

The second shipping address line

string
$ShippingCity

The city of the shipping address

string
$ShippingCompany

The company of the person on the shipping address

string
$ShippingCountry

The ISO code of the country of the shipping address

string
$ShippingCountryCode

The phone initials of the country of the shipping address (+XX)

string
$ShippingCvr

The shipping cvr

string
$ShippingEan

The shipping ean

string
$ShippingEmail

The email of the shipping address

string
$ShippingFirstname

The firstname of the person on the shipping address

string
$ShippingLastname

The lastname of the person on the shipping address

string
$ShippingMobile

The mobile number of the shipping address

string
$ShippingPhone

The phone number of the shipping address

string
$ShippingReferenceNumber

TheReference of the shipping address

string
$ShippingState

The shortcode of the state of the shipping adress

string
$ShippingType

The type of the address (currently always 'delivery')

string
$ShippingZip

The zipcode of the shipping address

int
$Site

The site of the User

string
$Type

The Type of the User ('Private', 'Company', 'var Sector')

string
$Url

The url of the Users webpage

int
$UserGroupId

The id of the UserGroup of the User

string
$Username

The usrename of the User

string
$Zip

The zip of the User

Documentation generated on Thu, 26 Jul 2018 13:22:50 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)

---
## Source: https://api.hostedshop.io/doc/Hosted%20Solution%20API/UserCreate.html

## Class UserCreate

Description

Description |
[Vars](#sec-var-summary) ([details](#sec-vars))

This Class represents a dataobject for creating a User.

Variable Summary

[Description](#sec-description) |
Vars ([details](#sec-vars))

string
[$Address](#$Address "details")

string
[$Address2](#$Address2 "details")

boolean
[$Approved](#$Approved "details")

string
[$BirthDate](#$BirthDate "details")

string
[$City](#$City "details")

string
[$Company](#$Company "details")

boolean
[$Consent](#$Consent "details")

datetime
[$ConsentDate](#$ConsentDate "details")

string
[$Country](#$Country "details")

int
[$CountryCode](#$CountryCode "details")

int
[$Currency](#$Currency "details")

string
[$Cvr](#$Cvr "details")

string
[$DateCreated](#$DateCreated "details")

string
[$DateUpdated](#$DateUpdated "details")

string
[$Description](#$Description "details")

int
[$DiscountGroupId](#$DiscountGroupId "details")

string
[$Ean](#$Ean "details")

string
[$Email](#$Email "details")

string
[$Fax](#$Fax "details")

string
[$Firstname](#$Firstname "details")

int[]
[$InterestFields](#$InterestFields "details")

String[]
[$LanguageAccess](#$LanguageAccess "details")

string
[$LanguageISO](#$LanguageISO "details")

string
[$Lastname](#$Lastname "details")

string
[$Mobile](#$Mobile "details")

boolean
[$Newsletter](#$Newsletter "details")

string
[$Number](#$Number "details")

string
[$Password](#$Password "details")

string
[$Phone](#$Phone "details")

string
[$Referer](#$Referer "details")

string
[$SeoDescription](#$SeoDescription "details")

string
[SeoTitle](#$SeoTitle "details")

int
[$Sex](#$Sex "details")

int
[$Site](#$Site "details")

string
[$ShippingAddress](#$ShippingAddress "details")

string
[$ShippingAddress2](#$ShippingAddress2 "details")

string
[$ShippingCity](#$ShippingCity "details")

string
[$ShippingCompany](#$ShippingCompany "details")

string
[$ShippingCountry](#$ShippingCountry "details")

string
[$ShippingCountryCode](#$ShippingCountryCode "details")

string
[$ShippingCvr](#$ShippingCvr "details")

string
[$ShippingEan](#$ShippingEan "details")

string
[$ShippingEmail](#$ShippingEmail "details")

string
[$ShippingFirstname](#$ShippingFirstname "details")

string
[$ShippingLastname](#$ShippingLastname "details")

string
[$ShippingMobile](#$ShippingMobile "details")

string
[$ShippingPhone](#$ShippingPhone "details")

string
[$ShippingReferenceNumber](#$ShippingReferenceNumber "details")

string
[$ShippingState](#$ShippingState "details")

string
[$ShippingType](#$ShippingType "details")

string
[$ShippingZip](#$ShippingZip "details")

string
[$Type](#$Type "details")

string
[$Url](#$Url "details")

int
[$UserGroupId](#$UserGroupId "details")

string
[$Username](#$Username "details")

string
[$Zip](#$Zip "details")

Variables

[Description](#sec-description) |
Vars

string
$Address

The address of the User

string
$Address2

The second address line of the User

boolean
$Approved

Wether or not the user has been approved by an administrator

string
$BirthDate

The date of birth of the User

string
$City

The city of the User

string
$Company

The name of the company of this User

boolean
$Consent

The consent status of the user

datetime
$ConsentDate

The date that the user confirmed consent

string
$Country

The country of the User

int
$CountryCode

The phone initials of the Users country

int
$Currency

The currency iso of the User

string
$Cvr

The Cvr number of the User

string
$DateCreated

The datetime the User was created

string
$DateUpdated

The datetime the User was last updated

string
$Description

The description of the User

int
$DiscountGroupId

The id of the DiscountGroup of the User

string
$Ean

The Ean number of the User

string
$Email

The email of the User

string
$Fax

The fax number of the User

string
$Firstname

The firstname of the user

int[]
$InterestFields

The ids InterestGroups of this User

string
$LanguageISO

The selected frontend Language ISO of the User

string
$Lastname

The lastname of the user

string
$Mobile

The mobile number of the User

boolean
$Newsletter

Whether or not the User should receive Newsletters

string
$Number

The number of the User

string
$Password

The password of the User. This property will always return an empty result as to not expose sensitive data in regards to data security.

string
$Phone

The phone number of the User

string
$Referer

String describing from where the user was created

string
$SeoDescription

The SEO description of a brand type user

string
$SeoTitle

The SEO title of a brand type user

int
$Sex

Wether the User is male of female (1 for male, 2 for female, 0 for none)

int
$Site

The id of the site of the User. When provided together with LanguageISO, the combination is validated against available site/language configurations

string
$ShippingAddress

The shipping address

string
$ShippingAddress2

The second shipping address line

string
$ShippingCity

The city of the shipping address

string
$ShippingCompany

The company of the person on the shipping address

string
$ShippingCountry

The ISO code of the country of the shipping address

string
$ShippingCountryCode

The phone initials of the country of the shipping address (+XX)

string
$ShippingCvr

The shipping cvr

string
$ShippingEan

The shipping ean

string
$ShippingEmail

The email of the shipping address

string
$ShippingFirstname

The firstname of the person on the shipping address

string
$ShippingLastname

The lastname of the person on the shipping address

string
$ShippingMobile

The mobile number of the shipping address

string
$ShippingPhone

The phone number of the shipping address

string
$ShippingReferenceNumber

TheReference of the shipping address

string
$ShippingState

The shortcode of the state of the shipping adress

string
$ShippingType

The type of the address (currently always 'delivery')

string
$ShippingZip

The zipcode of the shipping address

string
$Type

The Type of the User ('Private', 'Company', 'var Sector')

string
$Url

The url of the Users webpage

int
$UserGroupId

The id of the UserGroup of the User

string
$Username

The username of the User

string
$Zip

The zip of the User

Documentation generated on Thu, 26 Jul 2018 13:22:51 +0000 by [phpDocumentor 1.4.4](http://www.phpdoc.org)