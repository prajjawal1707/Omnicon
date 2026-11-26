git remote add origin https://github.com/prajjawal1707/Omnicon.gitimport expense_manager

def main():
    while True:
        print("1. Add Expense")
        print("2. Edit Expense")
        print("3. Delete Expense")
        print("4. Monthly Expense Summary")
        print("5. Category-wise Report")
        print("6. Exit")
        
        choice = input("Choose an option: ")
        
        if choice == "1":
            expense_manager.add_expense()
        elif choice == "2":
            expense_manager.edit_expense()
        elif choice == "3":
            expense_manager.delete_expense()
        elif choice == "4":
            expense_manager.monthly_expense_summary()
        elif choice == "5":
            expense_manager.category_wise_report()
        elif choice == "6":
            break
        else:
            print("Invalid choice. Please choose a valid option.")

if __name__ == "__main__":
    main()
