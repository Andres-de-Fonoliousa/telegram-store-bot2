# seed.py
import json
from core.database import SessionLocal, engine, Base
from core import models

def seed_database():
    """
    This function reads products.json and fills the database.
    It's exactly like running 'php artisan db:seed' in Laravel.
    """
    
    # 1. Create all tables (like 'php artisan migrate')
    print("Creating tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    
    # 2. Open a database session (like starting a DB transaction)
    db = SessionLocal()
    
    try:
        # 3. Load the JSON file
        with open("products.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 4. Seed Categories
        print("Seeding categories...")
        for cat_data in data.get("categories", []):
            # Check if category already exists
            existing = db.query(models.Category).filter_by(id=cat_data["id"]).first()
            if not existing:
                category = models.Category(
                    id=cat_data["id"],
                    name_ar=cat_data["name_ar"],
                    name_en=cat_data.get("name_en", "")
                )
                db.add(category)
        
        db.commit()  # Save categories first
        
        # 5. Seed Products
        print("Seeding products...")
        for prod_data in data.get("products", []):
            import json as json_module
            fields_json = json_module.dumps(prod_data.get("fields", []))
            validation_json = json_module.dumps(prod_data.get("validation", {}))
            existing = db.query(models.Product).filter_by(id=prod_data["id"]).first()
            if not existing:
                product = models.Product(
                    id=prod_data["id"],
                    name_ar=prod_data["name_ar"],
                    description_ar=prod_data.get("description_ar", ""),
                    category_id=prod_data["category_id"],
                    base_price_syp=prod_data.get("base_price_syp", 0),
                    fields=fields_json,
                    validation=validation_json,
                    image_path=prod_data.get("image", ""),
                    is_active=True
                )
                db.add(product)
            else:
                existing.name_ar = prod_data["name_ar"]
                existing.fields = fields_json
                existing.validation = validation_json
        db.commit()

        # 6. Seed Example Prices for Each Product Option
        print("Seeding example prices for product options...")
        usd_example = 1.5
        syp_rate = 15000
        for prod_data in data.get("products", []):
            product_id = prod_data["id"]
            validation = prod_data.get("validation", {})
            # Find all fields with 'choice' type (options)
            for field, rule in validation.items():
                if rule.get("type") == "choice":
                    for option in rule.get("options", []):
                        # Example: price in USD = option * 0.01, SYP = USD * syp_rate
                        price_usd = float(option) * 0.01 if isinstance(option, (int, float)) else usd_example
                        price_syp = int(price_usd * syp_rate)
                        exists = db.query(models.ProductsPrice).filter_by(product_id=product_id, option_value=str(option)).first()
                        if not exists:
                            db.add(models.ProductsPrice(product_id=product_id, option_value=str(option), price_syp=price_syp))
        db.commit()
        print("✅ Database seeded successfully!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()