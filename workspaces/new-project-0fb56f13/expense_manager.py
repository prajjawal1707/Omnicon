class ExpenseManager:
    def __init__(self):
        self.expenses = {}

    def add_expense(self):
        name = input("Enter expense name: ")
        amount = float(input("Enter expense amount: "))
        category = input("Enter expense category: ")
        
        if name in self.expenses:
            print("Expense already exists. Please choose a different name.")
            return
        if amount <= 0:
            print("Invalid amount. Please enter a positive number.")
            return
        
        self.expenses[name] = {"amount": amount, "category": category}
        print("Expense added successfully.")

    def edit_expense(self):
        name = input("Enter expense name to edit: ")
        if name not in self.expenses:
            print("Expense not found. Please choose a valid expense.")
            return
        
        amount = float(input("Enter new expense amount: "))
        category = input("Enter new expense category: ")
        
        if amount <= 0:
            print("Invalid amount. Please enter a positive number.")
            return
        
        self.expenses[name]["amount"] = amount
        self.expenses[name]["category"] = category
        print("Expense edited successfully.")

    def delete_expense(self):
        name = input("Enter expense name to delete: ")
        if name not in self.expenses:
            print("Expense not found. Please choose a valid expense.")
            return
        
        del self.expenses[name]
        print("Expense deleted successfully.")

    def monthly_expense_summary(self):
        total_amount = 0
        for expense in self.expenses.values():
            total_amount += expense["amount"]
        print(f"Total monthly expense: {total_amount}")

    def category_wise_report(self):
        category_wise_expenses = {}
        for expense in self.expenses.values():
            if expense["category"] in category_wise_expenses:
                category_wise_expenses[expense["category"]] += expense["amount"]
            else:
                category_wise_expenses[expense["category"]] = expense["amount"]
        
        for category, amount in category_wise_expenses.items():
            print(f"Category: {category}, Total Amount: {amount}")

if __name__ == "__main__":
    expense_manager = ExpenseManager()
