/* The redemption-rule vocabulary for the admin Promotions page. The coupon
   model carries exactly one customer-redemption setting — the boolean
   once_per_customer, enforced server-side at checkout ("used" means a
   standing order carries the code; cancelling that order hands it back) —
   so the dropdown speaks two rules and nothing else. Selects are string
   valued; these helpers translate between the model's boolean and the
   option the admin picked. */

export const REDEMPTION_RULES = [
  { value: "repeatable", label: "No customer limit", once_per_customer: false },
  { value: "once", label: "1 redemption per customer", once_per_customer: true },
];

export const redemptionRule = (oncePerCustomer) =>
  oncePerCustomer ? "once" : "repeatable";

export const oncePerCustomerFromRule = (rule) => {
  const match = REDEMPTION_RULES.find((r) => r.value === rule);
  // An unknown token (hand-edited markup, a value from a future page)
  // reads as the backend's own default: repeatable.
  return match ? match.once_per_customer : false;
};
