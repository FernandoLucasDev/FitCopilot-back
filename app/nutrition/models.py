from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class NutritionPlan(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "nutrition_plans"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(db.ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(db.Text())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    previous_version_id: Mapped[str | None] = mapped_column(db.ForeignKey("nutrition_plans.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student = relationship("StudentProfile", back_populates="nutrition_plans")
    meals = relationship("NutritionPlanMeal", back_populates="nutrition_plan", cascade="all, delete-orphan")
    student_assignments = relationship("StudentNutritionPlan", back_populates="plan")


class NutritionPlanMeal(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "nutrition_plan_meals"

    nutrition_plan_id: Mapped[str] = mapped_column(db.ForeignKey("nutrition_plans.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(db.Text())

    nutrition_plan = relationship("NutritionPlan", back_populates="meals")
    food_items = relationship("NutritionPlanFoodItem", back_populates="nutrition_plan_meal", cascade="all, delete-orphan")


class NutritionPlanFoodItem(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "nutrition_plan_food_items"

    nutrition_plan_meal_id: Mapped[str] = mapped_column(db.ForeignKey("nutrition_plan_meals.id"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    food_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity_text: Mapped[str | None] = mapped_column(String(80))
    calories: Mapped[int | None] = mapped_column(Integer)
    protein_grams: Mapped[int | None] = mapped_column(Integer)
    carbs_grams: Mapped[int | None] = mapped_column(Integer)
    fats_grams: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(db.Text())

    nutrition_plan_meal = relationship("NutritionPlanMeal", back_populates="food_items")


class StudentNutritionPlan(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "student_nutrition_plans"

    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(db.ForeignKey("nutrition_plans.id"), nullable=False, index=True)
    assigned_by_user_id: Mapped[str | None] = mapped_column(db.ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    student = relationship("StudentProfile", back_populates="nutrition_plan_assignments")
    plan = relationship("NutritionPlan", back_populates="student_assignments")
